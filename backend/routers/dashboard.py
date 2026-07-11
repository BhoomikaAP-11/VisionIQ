"""
Dashboard generation endpoints.

GET  /api/dashboard/{session_id}/overview  -> Phase 4 auto-dashboard
POST /api/dashboard/{session_id}/query     -> Phase 9 question-driven dashboard
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import (
    ai_service,
    dashboard as dashboard_engine,
    excel_service,
    intent as intent_mod,
    query_classifier,
    query_log,
)
from ..services.sessions import store

LLM_CONFIDENCE_THRESHOLD = 0.6
CLASSIFIER_HIGH_CONFIDENCE = 0.55  # trust classifier at or above this

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


class QueryBody(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)
    sheet: str | None = None


@router.get("/{session_id}/overview")
def overview(session_id: str, sheet: str | None = None):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")
    if session.kind == "db" and session.data.get("dataframe") is None:
        raise HTTPException(400, "Load a table first via POST /api/db/{id}/load")

    df = store.get_dataframe(session_id, sheet)
    if df is None:
        raise HTTPException(404, "Sheet not found")
    sheet_name = sheet or session.profile.get("primary_sheet")
    sheet_profile = session.profile["sheets"][sheet_name]

    spec = dashboard_engine.build_executive_overview(df, sheet_profile)
    store.append_history(session_id, {"type": "overview", "sheet": sheet_name})
    return spec


@router.post("/{session_id}/query")
async def query(session_id: str, body: QueryBody):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")
    if session.kind == "db" and session.data.get("dataframe") is None:
        raise HTTPException(400, "Load a table first via POST /api/db/{id}/load")

    df = store.get_dataframe(session_id, body.sheet)
    if df is None:
        raise HTTPException(404, "Sheet not found")
    sheet_name = body.sheet or session.profile.get("primary_sheet")
    sheet_profile = session.profile["sheets"][sheet_name]

    # ------------------------------------------------------------------
    # Stage 0: fast heuristic FIRST — most analytical queries land here
    # and skip DistilBERT entirely, giving sub-100ms responses.
    # ------------------------------------------------------------------
    quick_intent = intent_mod.parse(body.question, sheet_profile)
    if quick_intent["confidence"] >= 0.85 and quick_intent["op"] not in {"greeting", "unknown"}:
        # Heuristic is confident enough — skip the classifier and BERT entirely
        classification = {
            "label": quick_intent["op"],
            "confidence": quick_intent["confidence"],
            "model": "heuristic-fast-path",
            "top3": [],
            "is_smalltalk": False,
            "is_analytical": True,
        }
    else:
        # Only invoke the ML classifier for ambiguous queries — catches
        # "hi", "what are you doing" etc. and routes them conversationally.
        classification = query_classifier.classify(body.question)
    if (
        classification["label"] == "smalltalk"
        and classification["confidence"] >= CLASSIFIER_HIGH_CONFIDENCE
    ):
        parsed = {
            "op": "greeting",
            "reply": query_classifier.canned_reply(body.question),
            "confidence": classification["confidence"],
            "source": "classifier",
            "raw": body.question,
        }
        spec = dashboard_engine.build_query_dashboard(df, sheet_profile, body.question, intent=parsed)
        query_log.log(body.question, classification, parsed)
        return spec
    if (
        classification["label"] == "unclear"
        and classification["confidence"] >= CLASSIFIER_HIGH_CONFIDENCE
    ):
        parsed = {
            "op": "unknown",
            "reply": (
                "That doesn't look like a data question. Try one of these:\n"
                "  • Top 10 <dimension> by <measure>\n"
                "  • Show <measure> trend by month\n"
                "  • Forecast <measure> for the next 6 months\n"
                "  • Find anomalies in <measure>"
            ),
            "confidence": classification["confidence"],
            "source": "classifier",
            "raw": body.question,
        }
        spec = dashboard_engine.build_query_dashboard(df, sheet_profile, body.question, intent=parsed)
        query_log.log(body.question, classification, parsed)
        return spec

    # ------------------------------------------------------------------
    # Detect follow-up commands
    # ------------------------------------------------------------------
    is_refinement = intent_mod.is_refinement(body.question)
    if intent_mod.is_reset(body.question):
        session.active_filters = []
        session.last_intent = None

    new_filters = intent_mod.extract_filters(body.question, sheet_profile, df)
    if is_refinement and new_filters:
        # Merge: drop existing filters on the same column, then add the new ones
        new_cols = {f["column"] for f in new_filters}
        session.active_filters = [f for f in session.active_filters if f["column"] not in new_cols]
        session.active_filters.extend(new_filters)

    # ------------------------------------------------------------------
    # Parse intent (or reuse the last one if this is a pure refinement)
    # ------------------------------------------------------------------
    parsed = intent_mod.parse(body.question, sheet_profile)
    if is_refinement and session.last_intent and parsed["op"] in {"summary", "unknown"}:
        # Carry over last intent; refinements modify rather than restart
        parsed = {**session.last_intent, **{k: v for k, v in parsed.items() if v not in (None, "summary", "unknown")}}
        parsed["confidence"] = max(parsed.get("confidence", 0.5), 0.7)
        parsed["source"] = "follow-up"

    # ------------------------------------------------------------------
    # BERT rescue: if heuristic couldn't figure out an op but the classifier
    # is confident it's a specific analytical intent, promote the BERT label.
    # Catches typos ('forcaste 8 months') the regex missed.
    # ------------------------------------------------------------------
    _BERT_TO_OP = {
        "top_bottom": "top",
        "trend": "trend",
        "forecast": "forecast",
        "anomaly": "anomaly",
        "correlation": "correlation",
        "root_cause": "root_cause",
        "overview": "overview",
        "insights": "insights",
        "explain": "explain",
        "raw_data": "summary",
    }
    if (
        parsed["op"] in {"summary", "unknown"}
        and classification.get("model") in {"distilbert", "sklearn"}
        and classification.get("confidence", 0) >= 0.7
    ):
        rescued = _BERT_TO_OP.get(classification["label"])
        if rescued:
            parsed["op"] = rescued
            parsed["confidence"] = max(parsed["confidence"], 0.8)
            parsed["source"] = "classifier-rescue"

    # ------------------------------------------------------------------
    # LLM fallback for low confidence
    # ------------------------------------------------------------------
    if parsed["confidence"] < LLM_CONFIDENCE_THRESHOLD and parsed["op"] not in {"greeting", "overview", "insights", "explain"}:
        try:
            schema_context = excel_service.build_schema_context({"profile": session.profile})
            llm_intent = await ai_service.parse_intent_with_llm(body.question, schema_context)
            if llm_intent.get("op") and llm_intent["op"] not in {"unknown"}:
                for key in ("op", "measure", "dimension", "date_col", "n",
                            "periods", "nth_index", "ascending", "reply"):
                    if llm_intent.get(key) is not None:
                        parsed[key] = llm_intent[key]
                parsed["confidence"] = max(parsed["confidence"], llm_intent.get("confidence", 0.85))
                parsed["source"] = "llm"
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Apply accumulated filters to the dataframe
    # ------------------------------------------------------------------
    filtered_df = _apply_filters(df, session.active_filters)

    spec = dashboard_engine.build_query_dashboard(filtered_df, sheet_profile, body.question, intent=parsed)
    spec["active_filters"] = list(session.active_filters)
    if is_refinement:
        spec["title"] = f"{spec['title']} (refined)"

    # Remember non-greeting intent for future follow-ups
    if parsed["op"] not in {"greeting", "unknown"}:
        session.last_intent = {k: v for k, v in parsed.items() if k != "raw"}

    store.append_history(session_id, {
        "type": "query",
        "question": body.question,
        "sheet": sheet_name,
        "intent": parsed,
        "filters": list(session.active_filters),
    })
    query_log.log(body.question, classification, parsed)
    return spec


def _apply_filters(df, filters: list[dict]):
    """Apply session-level filters in order. Returns a new DataFrame."""
    import pandas as pd
    if not filters or df is None:
        return df
    out = df
    for f in filters:
        col = f["column"]
        if col not in out.columns:
            continue
        op = f.get("op")
        val = f.get("value")
        try:
            if op == "year_eq":
                series = pd.to_datetime(out[col], errors="coerce")
                out = out[series.dt.year == int(val)]
            elif op == "eq":
                out = out[out[col].astype(str).str.lower() == str(val).lower()]
            elif op == "neq":
                out = out[out[col].astype(str).str.lower() != str(val).lower()]
            elif op == "compare_yoy":
                # Leave compare_yoy as a flag for the engine; doesn't filter here
                continue
        except Exception:
            continue
    return out


@router.get("/{session_id}/history")
def history(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")
    return {"history": session.history}
