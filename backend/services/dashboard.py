"""
Dashboard spec generator (Master Prompt Phases 4, 9, 10, 18).

Produces a JSON spec the frontend can render. Each chart entry is a
self-describing block: {id, type, title, x, y, data, why}. The frontend
chooses the actual chart library; this layer only decides *what* to show
and prepares the data.
"""
from __future__ import annotations

import uuid
from typing import Optional

import pandas as pd

from . import analytics, intent as intent_mod
from .profiling import safe_val, records


def _id() -> str:
    return uuid.uuid4().hex[:8]


def _pick_primary_measure(profile: dict) -> Optional[str]:
    """Prefer revenue/sales/profit/total when present; otherwise first measure."""
    measures = profile["classification"]["measures"]
    if not measures:
        return None
    priority = ["revenue", "sales", "total", "amount", "profit", "income"]
    for kw in priority:
        for m in measures:
            if kw in m.lower():
                return m
    return measures[0]


def _pick_primary_date(profile: dict) -> Optional[str]:
    dates = profile["classification"]["date_columns"]
    return dates[0] if dates else None


def _pick_primary_dimension(profile: dict, exclude: Optional[list[str]] = None) -> Optional[str]:
    exclude = exclude or []
    dims = [d for d in profile["classification"]["dimensions"] if d not in exclude]
    if not dims:
        return None
    # Prefer category-like names
    priority = ["category", "segment", "product", "region", "channel", "type", "department"]
    for kw in priority:
        for d in dims:
            if kw in d.lower():
                return d
    return dims[0]


# ---------------------------------------------------------------------------
# Chart-builder helpers
# ---------------------------------------------------------------------------
def _kpi_card(kpi: dict) -> dict:
    return {
        "id": _id(),
        "type": "kpi_card",
        "title": kpi["name"],
        "value": kpi["value"],
        "trend": kpi["trend"],
        "change_pct": kpi["change_pct"],
        "sparkline": kpi["sparkline"],
        "why": f"{kpi['name']} is a primary measure; trend computed from recent periods.",
    }


def _trend_chart(t: dict) -> dict:
    return {
        "id": _id(),
        "type": "line",
        "title": f"{t['measure']} over time",
        "x": "x",
        "y": "y",
        "series": ["y", "moving_avg"],
        "data": t["series"],
        "why": "Time-series trend lets viewers see momentum and seasonality at a glance.",
    }


def _top_chart(rows: list[dict], dimension: str, measure: str, n: int) -> dict:
    return {
        "id": _id(),
        "type": "bar",
        "title": f"Top {n} {dimension} by {measure}",
        "x": dimension,
        "y": measure,
        "data": rows,
        "why": f"Bar chart ranks {dimension} by {measure} so the biggest contributors are obvious.",
    }


def _pareto_chart(p: dict) -> dict:
    return {
        "id": _id(),
        "type": "pareto",
        "title": f"Pareto: {p['dimension']} contribution to {p['measure']}",
        "x": p["dimension"],
        "y": p["measure"],
        "data": p["items"],
        "summary": f"{p['top_80pct_count']} {p['dimension']} drive 80% of {p['measure']}",
        "why": "Pareto reveals the vital few that drive most of the total — classic 80/20.",
    }


def _correlation_heatmap(corr: dict) -> dict:
    return {
        "id": _id(),
        "type": "heatmap",
        "title": "Correlation between measures",
        "columns": corr["columns"],
        "data": corr["matrix"],
        "strong_pairs": corr.get("strong_pairs", []),
        "why": "Highlights which measures move together — useful for finding drivers and proxies.",
    }


def _histogram_chart(h: dict) -> dict:
    return {
        "id": _id(),
        "type": "histogram",
        "title": f"Distribution of {h['measure']}",
        "x": "range",
        "y": "count",
        "data": h["bins"],
        "why": "Distribution shape reveals skew, multi-modality, and concentration.",
    }


def _missing_chart(quality: dict) -> dict:
    data = [
        {"column": c, "null_pct": p}
        for c, p in quality["null_percentage"].items()
        if p > 0
    ]
    data.sort(key=lambda r: r["null_pct"], reverse=True)
    return {
        "id": _id(),
        "type": "bar",
        "title": "Missing values by column",
        "x": "column",
        "y": "null_pct",
        "data": data[:20],
        "why": "Surfaces data-quality gaps that could distort downstream analysis.",
    }


# ---------------------------------------------------------------------------
# Insight & recommendation generators (computed, never fabricated)
# ---------------------------------------------------------------------------
def _build_insights(df: pd.DataFrame, profile: dict, computed: dict) -> list[str]:
    out: list[str] = []
    q = profile["quality"]
    if q["duplicate_rows"]:
        out.append(f"{q['duplicate_rows']} duplicate rows detected — dedup before reporting.")
    for col, pct in q["null_percentage"].items():
        if pct > 30:
            out.append(f"`{col}` is missing in {pct}% of rows.")
            break

    for kpi in computed.get("kpis", []):
        if kpi["change_pct"] is not None:
            direction = "increased" if kpi["change_pct"] > 0 else "decreased"
            out.append(
                f"{kpi['name']} {direction} {abs(kpi['change_pct'])}% vs the prior period."
            )

    pareto_data = computed.get("pareto")
    if pareto_data and pareto_data.get("top_80pct_count"):
        out.append(
            f"Just {pareto_data['top_80pct_count']} {pareto_data['dimension']} values "
            f"drive 80% of {pareto_data['measure']}."
        )

    corr = computed.get("correlation")
    if corr:
        for pair in corr.get("strong_pairs", [])[:2]:
            sign = "positively" if pair["r"] > 0 else "negatively"
            out.append(f"`{pair['a']}` and `{pair['b']}` are {sign} correlated (r={pair['r']}).")

    anoms = computed.get("anomalies")
    if anoms and anoms.get("count"):
        out.append(f"{anoms['count']} anomalies detected in {anoms['measure']} (>3σ).")

    return out[:8]


def _build_recommendations(profile: dict, computed: dict) -> list[str]:
    recs: list[str] = []
    domain = profile["domain"]["primary"]
    pareto_data = computed.get("pareto")
    if pareto_data and pareto_data.get("top_80pct_count"):
        recs.append(
            f"Focus retention & upsell on the top {pareto_data['top_80pct_count']} "
            f"{pareto_data['dimension']} that drive most {pareto_data['measure']}."
        )
    for kpi in computed.get("kpis", []):
        if kpi["change_pct"] is not None and kpi["change_pct"] < -5:
            recs.append(
                f"Investigate decline in {kpi['name']} ({kpi['change_pct']}%) — "
                f"check pricing, channel mix, and recent campaigns."
            )

    domain_playbook = {
        "sales": "Run a cohort view to see if new vs returning customers behave differently.",
        "finance": "Layer in a variance vs budget chart next; raw totals hide overruns.",
        "retail": "Cross-reference inventory turnover with the top SKUs to avoid stockouts.",
        "hr": "Slice attrition by department and tenure band to localise the issue.",
        "marketing": "Compare CAC and conversion by channel before reallocating spend.",
        "logistics": "Overlay on-time-delivery against carrier to spot underperformers.",
    }
    if domain in domain_playbook:
        recs.append(domain_playbook[domain])

    if not recs:
        recs.append("Add a date filter and re-segment by the largest dimension to find the driver.")
    return recs[:6]


def _build_suggested_questions(profile: dict) -> list[str]:
    measure = _pick_primary_measure(profile)
    dim = _pick_primary_dimension(profile)
    date = _pick_primary_date(profile)
    suggestions = []
    if measure and date:
        suggestions.append(f"Show {measure} trend by month")
        suggestions.append(f"Forecast {measure} for the next 6 months")
    if measure and dim:
        suggestions.append(f"Top 10 {dim} by {measure}")
        suggestions.append(f"Why did {measure} change recently?")
    suggestions.append("Find anomalies in the data")
    suggestions.append("Show correlations between measures")
    return suggestions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_executive_overview(df: pd.DataFrame, profile: dict) -> dict:
    """
    Phase 4: produce the initial dashboard without any user question.
    Returns a complete dashboard spec.
    """
    classified = profile["classification"]
    measures = classified["measures"]
    date_col = _pick_primary_date(profile)
    primary_measure = _pick_primary_measure(profile)
    primary_dim = _pick_primary_dimension(profile)

    # --- compute analytics ---
    computed: dict = {}
    computed["kpis"] = analytics.kpi_summary(df, measures, date_col=date_col)

    charts: list[dict] = [_kpi_card(k) for k in computed["kpis"]]

    if primary_measure and date_col:
        t = analytics.trend(df, date_col, primary_measure)
        if t.get("series"):
            charts.append(_trend_chart(t))

    if primary_measure and primary_dim:
        top = analytics.top_n(df, primary_dim, primary_measure, n=10)
        if top:
            charts.append(_top_chart(top, primary_dim, primary_measure, 10))
        p = analytics.pareto(df, primary_dim, primary_measure)
        if p.get("items"):
            computed["pareto"] = p
            charts.append(_pareto_chart(p))

    if len(measures) >= 2:
        corr = analytics.correlation_matrix(df, measures)
        if corr.get("matrix"):
            computed["correlation"] = corr
            charts.append(_correlation_heatmap(corr))

    if primary_measure:
        h = analytics.histogram(df, primary_measure)
        if h.get("bins"):
            charts.append(_histogram_chart(h))
        a = analytics.anomalies(df, primary_measure, date_col=date_col)
        computed["anomalies"] = a

    if any(p > 0 for p in profile["quality"]["null_percentage"].values()):
        charts.append(_missing_chart(profile["quality"]))

    # --- filters (slicers) ---
    filters = []
    for d in classified["dimensions"][:6]:
        non_null = df[d].dropna()
        if len(non_null):
            uniques = non_null.astype(str).unique().tolist()
            filters.append({
                "column": d,
                "type": "categorical",
                "values": uniques[:50],
            })
    if date_col:
        dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
        if len(dates):
            filters.append({
                "column": date_col,
                "type": "date_range",
                "min": str(dates.min().date()),
                "max": str(dates.max().date()),
            })

    # --- drill hierarchies ---
    drilldowns = []
    if date_col:
        drilldowns.append({"name": "Time", "levels": [
            f"{date_col}__Year", f"{date_col}__Quarter", f"{date_col}__Month"
        ]})
    geo_cols = [c["name"] for c in profile["columns"] if c["semantic_type"] == "geo"]
    if len(geo_cols) >= 2:
        drilldowns.append({"name": "Geography", "levels": geo_cols[:4]})

    spec = {
        "id": _id(),
        "title": "Executive Overview",
        "business_goal": (
            f"Give decision-makers a one-glance view of the {profile['domain']['primary']} "
            f"dataset: scale, trend, top contributors, quality, and anomalies."
        ),
        "generated_for": profile["name"],
        "domain": profile["domain"],
        "quality_panel": {
            "score": profile["quality"]["quality_score"],
            "total_rows": profile["quality"]["total_rows"],
            "total_columns": profile["quality"]["total_columns"],
            "duplicates": profile["quality"]["duplicate_rows"],
            "issues": profile["quality"]["issues"],
        },
        "kpis": computed["kpis"],
        "charts": charts,
        "filters": filters,
        "drilldowns": drilldowns,
        "insights": _build_insights(df, profile, computed),
        "recommendations": _build_recommendations(profile, computed),
        "suggested_questions": _build_suggested_questions(profile),
        "anomalies": computed.get("anomalies", {}),
        "explainability": {
            "kpi_selection": "Top measures chosen by name priority (revenue/sales/profit) then by index.",
            "chart_selection": "Trend for time-series, Bar for ranking, Pareto for contribution, Heatmap for correlation, Histogram for distribution.",
            "filter_selection": "Low-cardinality dimensions and detected date columns become slicers.",
            "recommendation_basis": "Computed deltas, Pareto cuts, anomaly counts, and domain playbook.",
        },
    }
    return spec


def build_query_dashboard(
    df: pd.DataFrame,
    profile: dict,
    question: str,
    intent: Optional[dict] = None,
) -> dict:
    """
    Phase 9: build a dashboard in response to a user question.
    Uses the new intent parser; honours ascending, n, periods, nth_index, and
    returns a conversational spec for greetings / unknown queries.
    """
    intent = intent or intent_mod.parse(question, profile)
    op = intent.get("op", "summary")

    # ------------------------------------------------------------------
    # Conversational ops short-circuit
    # ------------------------------------------------------------------
    if op in {"greeting", "unknown"}:
        return {
            "id": _id(),
            "title": "Assistant",
            "business_goal": "Conversational response — no dashboard generated.",
            "question": question,
            "intent": intent,
            "kpis": [],
            "charts": [],
            "filters": [],
            "drilldowns": [],
            "conversational": True,
            "reply": intent.get("reply") or "I’m here to help. Ask me a data question.",
            "insights": [],
            "recommendations": [],
            "suggested_questions": _build_suggested_questions(profile),
        }

    measure = intent.get("measure") or _pick_primary_measure(profile)
    dimension = intent.get("dimension") or _pick_primary_dimension(profile)
    date_col = intent.get("date_col") or _pick_primary_date(profile)
    ascending = bool(intent.get("ascending", False))

    charts: list[dict] = []
    computed: dict = {}
    query_result_rows: list[dict] = []

    # Meta-ops short-circuit to overview-style output
    if op == "overview":
        return build_executive_overview(df, profile)

    if op in {"insights", "explain"}:
        full = build_executive_overview(df, profile)
        # Insights-focused: drop most charts, keep just KPIs + trend + insights/recs
        keep_types = {"kpi_card", "line"}
        full["charts"] = [c for c in full["charts"] if c["type"] in keep_types]
        full["title"] = "Key insights" if op == "insights" else "Explanation"
        full["business_goal"] = (
            "Surface the most important takeaways from the dataset."
            if op == "insights"
            else "Walk through what the dashboard is showing and what it implies."
        )
        return full

    if op == "trend" and measure and date_col:
        t = analytics.trend(df, date_col, measure)
        if t.get("series"):
            charts.append(_trend_chart(t))
            query_result_rows = t["series"]

    elif op == "forecast" and measure and date_col:
        periods = int(intent.get("periods") or 6)
        f = analytics.forecast(df, date_col, measure, periods=periods)
        charts.append({
            "id": _id(),
            "type": "forecast",
            "title": f"{measure} forecast — next {periods} months ({f.get('method', 'forecast')})",
            "history": f.get("history", []),
            "data": f.get("forecast", []),
            "accuracy": f.get("accuracy"),
            "why": f"Projection {periods} periods ahead based on observed history.",
        })
        query_result_rows = f.get("forecast", [])

    elif op == "top" and measure and dimension:
        n = int(intent.get("n") or 10)
        rows = analytics.top_n(df, dimension, measure, n=n, ascending=ascending)
        direction = "Bottom" if ascending else "Top"
        chart = _top_chart(rows, dimension, measure, n)
        chart["title"] = f"{direction} {n} {dimension} by {measure}"
        chart["why"] = (
            f"Ranked {dimension} by {measure} ascending (smallest first)."
            if ascending else
            f"Ranked {dimension} by {measure} descending (largest first)."
        )
        charts.append(chart)
        query_result_rows = rows

    elif op == "nth" and measure and dimension:
        n_idx = int(intent.get("nth_index") or 1)
        # Sort by measure descending then pick exact position
        all_rows = analytics.top_n(df, dimension, measure, n=10_000, ascending=ascending)
        if 1 <= n_idx <= len(all_rows):
            single = [all_rows[n_idx - 1]]
            charts.append({
                "id": _id(),
                "type": "bar",
                "title": f"{n_idx}{_ord_suffix(n_idx)} {dimension} by {measure}",
                "x": dimension,
                "y": measure,
                "data": single,
                "why": f"Exact rank position {n_idx} (not top-{n_idx}).",
            })
            query_result_rows = single
        else:
            charts.append({
                "id": _id(),
                "type": "info",
                "title": f"Only {len(all_rows)} rows available",
                "data": [],
                "why": f"You asked for position {n_idx} but only {len(all_rows)} rows exist.",
            })

    elif op == "anomaly":
        target = measure or _pick_primary_measure(profile)
        if target:
            a = analytics.anomalies(df, target, date_col=date_col)
            computed["anomalies"] = a
            charts.append({
                "id": _id(),
                "type": "anomaly_table",
                "title": f"Anomalies in {target}"
                         + (f" — {a.get('count', 0)} found" if a.get('count') else " — none above 3σ"),
                "data": a.get("anomalies", []),
                "why": f"Z-score > 3 from mean {a.get('mean')} (σ={a.get('std')}). "
                       f"{a.get('count', 0)} flagged.",
            })
            query_result_rows = a.get("anomalies", [])
        else:
            charts.append(_no_measure_card())

    elif op == "correlation":
        corr = analytics.correlation_matrix(df, profile["classification"]["measures"])
        if corr.get("matrix"):
            computed["correlation"] = corr
            charts.append(_correlation_heatmap(corr))

    else:  # summary / fallback — pick the most useful chart for what we DID catch
        if measure and dimension:
            # Bar of measure by dimension
            top = analytics.top_n(df, dimension, measure, n=10, ascending=ascending)
            ch = _top_chart(top, dimension, measure, 10)
            ch["title"] = f"{measure} by {dimension}"
            ch["why"] = "Best-fit chart given the measure and dimension you mentioned."
            charts.append(ch)
            query_result_rows = top
        elif measure and date_col:
            # Trend of that measure
            t = analytics.trend(df, date_col, measure)
            if t.get("series"):
                charts.append(_trend_chart(t))
                query_result_rows = t["series"]
        elif measure:
            # Just the KPI for that measure
            kpis = analytics.kpi_summary(df, [measure], date_col=date_col)
            computed["kpis"] = kpis
            charts.extend(_kpi_card(k) for k in kpis)
            # Plus its distribution
            h = analytics.histogram(df, measure)
            if h.get("bins"):
                charts.append(_histogram_chart(h))
        elif dimension:
            # Counts by dimension
            primary_measure = _pick_primary_measure(profile)
            if primary_measure:
                top = analytics.top_n(df, dimension, primary_measure, n=10, ascending=ascending)
                charts.append(_top_chart(top, dimension, primary_measure, 10))
                query_result_rows = top
        else:
            # Nothing matched — give a useful overview rather than nothing
            kpis = analytics.kpi_summary(df, profile["classification"]["measures"], date_col=date_col)
            computed["kpis"] = kpis
            charts.extend(_kpi_card(k) for k in kpis)
            pm = _pick_primary_measure(profile)
            pd_ = _pick_primary_dimension(profile)
            if pm and pd_:
                top = analytics.top_n(df, pd_, pm, n=10)
                charts.append(_top_chart(top, pd_, pm, 10))
                query_result_rows = top

    # Query-specific insights & recommendations
    insights = _query_specific_insights(intent, query_result_rows, computed, df, profile)
    recommendations = _query_specific_recs(intent, query_result_rows, profile)

    spec = {
        "id": _id(),
        "title": question.strip().rstrip("?") or "Query Result",
        "business_goal": f"Answer: {question}",
        "question": question,
        "intent": intent,
        "kpis": computed.get("kpis", []),
        "charts": charts,
        "result_preview": query_result_rows[:25],
        "filters": [],
        "drilldowns": [],
        "insights": insights,
        "recommendations": recommendations,
        "suggested_questions": _build_suggested_questions(profile),
    }
    return spec


def _ord_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _no_measure_card() -> dict:
    return {
        "id": _id(),
        "type": "info",
        "title": "No numeric measure detected",
        "data": [],
        "why": "Anomaly detection needs at least one numeric column.",
    }


def _query_specific_insights(intent: dict, rows: list[dict], computed: dict, df, profile) -> list[str]:
    """Insights that describe THIS query's result, not just the dataset."""
    op = intent.get("op")
    measure = intent.get("measure")
    dimension = intent.get("dimension")
    out: list[str] = []

    if op == "top" and rows and dimension and measure:
        ranked = [r for r in rows if r.get(dimension) is not None]
        if ranked:
            top1 = ranked[0]
            top1_val = top1.get(measure, "—")
            direction = "lowest" if intent.get("ascending") else "highest"
            out.append(
                f"{top1.get(dimension)} has the {direction} {measure} ({top1_val})."
            )
            if len(ranked) >= 3:
                vals = [r.get(measure, 0) or 0 for r in ranked]
                total = sum(vals) or 1
                top3_share = sum(vals[:3]) / total * 100
                out.append(f"The top 3 contribute {top3_share:.1f}% of the shown total.")

    elif op == "forecast" and rows:
        first_y = rows[0].get("y")
        last_y = rows[-1].get("y")
        if first_y is not None and last_y is not None and first_y != 0:
            change = (last_y - first_y) / abs(first_y) * 100
            direction = "grow" if change > 0 else "decline"
            out.append(
                f"Forecast suggests {measure or 'the measure'} will {direction} "
                f"by ~{abs(change):.1f}% over the horizon."
            )

    elif op == "anomaly":
        a = computed.get("anomalies", {})
        if a.get("count"):
            out.append(f"{a['count']} anomalies found in {a.get('measure')} (|z| > 3).")
        else:
            out.append(f"No anomalies above 3σ in {measure or 'the measure'} — the series is stable.")

    elif op == "correlation":
        corr = computed.get("correlation", {})
        pairs = corr.get("strong_pairs", [])
        if pairs:
            top_pair = pairs[0]
            sign = "positively" if top_pair["r"] > 0 else "negatively"
            out.append(
                f"Strongest signal: {top_pair['a']} and {top_pair['b']} are "
                f"{sign} correlated (r={top_pair['r']})."
            )
        else:
            out.append("No measure pairs cross the |r|=0.6 threshold.")

    elif op == "trend" and rows:
        first_y = rows[0].get("y")
        last_y = rows[-1].get("y")
        if first_y and last_y and first_y != 0:
            change = (last_y - first_y) / abs(first_y) * 100
            out.append(
                f"{measure or 'Measure'} changed by {change:+.1f}% from "
                f"{rows[0].get('x')} to {rows[-1].get('x')}."
            )

    if not out:
        # Fall back to dataset-level insights
        out = _build_insights(df, profile, computed)
    return out[:6]


def _query_specific_recs(intent: dict, rows: list[dict], profile: dict) -> list[str]:
    op = intent.get("op")
    measure = intent.get("measure")
    dimension = intent.get("dimension")
    recs: list[str] = []

    if op == "top" and not intent.get("ascending") and rows and dimension:
        recs.append(f"Concentrate retention / expansion budget on the leading {dimension}s above.")
    if op == "top" and intent.get("ascending") and rows and dimension:
        recs.append(f"Investigate the underperforming {dimension}s — root-cause analysis warranted.")
    if op == "forecast":
        recs.append(f"Compare this forecast against budget and plan capacity accordingly.")
    if op == "anomaly":
        recs.append("Open each flagged anomaly to confirm whether it's a data issue or a real event.")
    if op == "correlation":
        recs.append("Use the strongest correlated pair as a leading indicator in your KPI tree.")
    if op == "trend":
        recs.append("Layer a moving average on top to separate signal from monthly noise.")

    if not recs:
        recs = _build_recommendations(profile, {})
    return recs[:5]


# ---------------------------------------------------------------------------
# Heuristic NL intent — used when AI is unavailable or as a fast pre-filter
# ---------------------------------------------------------------------------
def _heuristic_intent(question: str, profile: dict) -> dict:
    q = question.lower()
    intent: dict = {"op": "summary"}

    if any(k in q for k in ["forecast", "predict", "next month", "next year", "future"]):
        intent["op"] = "forecast"
    elif any(k in q for k in ["trend", "over time", "by month", "by year", "history"]):
        intent["op"] = "trend"
    elif any(k in q for k in ["top", "highest", "biggest", "best", "leading"]):
        intent["op"] = "top"
    elif any(k in q for k in ["bottom", "worst", "lowest"]):
        intent["op"] = "top"
        intent["ascending"] = True
    elif any(k in q for k in ["anomaly", "outlier", "unusual"]):
        intent["op"] = "anomaly"
    elif any(k in q for k in ["correlation", "related", "driver"]):
        intent["op"] = "correlation"

    # crude column matching
    for col in profile["classification"]["measures"]:
        if col.lower() in q:
            intent["measure"] = col
            break
    for col in profile["classification"]["dimensions"]:
        if col.lower() in q:
            intent["dimension"] = col
            break
    for col in profile["classification"]["date_columns"]:
        if col.lower() in q:
            intent["date_col"] = col
            break

    # number extraction for top N
    import re
    m = re.search(r"top\s+(\d+)", q)
    if m:
        intent["n"] = int(m.group(1))

    return intent
