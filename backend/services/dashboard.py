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


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    """Wrap a KPI dict into a chart spec. Tolerates missing optional fields
    (count-based KPIs from HR/survey data don't have sparklines or trends)."""
    return {
        "id": _id(),
        "type": "kpi_card",
        "title": kpi.get("name", "KPI"),
        "value": kpi.get("value"),
        "trend": kpi.get("trend", "stable"),
        "change_pct": kpi.get("change_pct"),
        "change_abs": kpi.get("change_abs"),
        "current_value": kpi.get("current_value", kpi.get("value")),
        "previous_value": kpi.get("previous_value"),
        "compare_period_label": kpi.get("compare_period_label"),
        "compare_current_period": kpi.get("compare_current_period"),
        "compare_previous_period": kpi.get("compare_previous_period"),
        "note": kpi.get("note"),
        "sparkline": kpi.get("sparkline", []),
        "why": f"{kpi.get('name', 'KPI')}",
    }


def _trend_chart(t: dict) -> dict:
    return {
        "id": _id(),
        "type": "line",
        "title": f"{t['measure']} over time",
        "x": "x",
        "y": "y",
        "measure": t["measure"],
        "date_col": t.get("date_col"),
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
        "measure": p["measure"],
        "data": p["items"],
        "total_items": p.get("total_items"),
        "class_counts": p.get("class_counts"),
        "summary": (
            f"{p['top_80pct_count']} of {p.get('total_items')} {p['dimension']} "
            f"({p.get('top_80pct_share_of_items')}%) drive 80% of {p['measure']}"
        ),
        "why": (
            "Pareto reveals the vital few that drive most of the total. "
            f"Class A: {p.get('class_counts', {}).get('A', 0)} · "
            f"B: {p.get('class_counts', {}).get('B', 0)} · "
            f"C: {p.get('class_counts', {}).get('C', 0)}."
        ),
    }


def _correlation_heatmap(corr: dict) -> dict:
    level = corr.get("level", "by-row")
    subtitle = {
        "by-row": "computed per-row (raw)",
        "by-month": "computed per month",
    }.get(level, f"computed per {corr.get('aggregate_by') or level}")
    return {
        "id": _id(),
        "type": "heatmap",
        "title": f"Correlation between measures — {subtitle}",
        "columns": corr["columns"],
        "data": corr["matrix"],
        "row_level_matrix": corr.get("row_level_matrix"),
        "level": level,
        "aggregate_by": corr.get("aggregate_by"),
        "strong_pairs": corr.get("strong_pairs", []),
        "why": corr.get("note") or "Highlights which measures move together — useful for finding drivers and proxies.",
    }


def _histogram_chart(h: dict) -> dict:
    interp = h.get("interpretation", {})
    obs = interp.get("observations", [])
    summary = " ".join(obs) if obs else "Distribution shape reveals skew, multi-modality, and concentration."
    return {
        "id": _id(),
        "type": "histogram",
        "title": f"Distribution of {h['measure']}",
        "x": "range",
        "y": "count",
        "data": h["bins"],
        "interpretation": interp,
        "why": summary,
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
def _classify_insight(text: str) -> str:
    """Simple keyword classifier: 'positive', 'negative', 'warning', 'neutral'."""
    t = text.lower()
    if any(w in t for w in ["duplicate", "missing", "anomal", "outlier", "partial", "warning"]):
        return "warning"
    if any(w in t for w in ["drag", "decrease", "declin", "drop", "fall", "worst", "hurt", "loss"]):
        return "negative"
    if any(w in t for w in ["increas", "grew", "rise", "gain", "leader", "top", "positively correlated", "contribut"]):
        return "positive"
    return "neutral"


def _tagged(text: str) -> dict:
    """Wrap an insight string with a category tag."""
    return {"kind": _classify_insight(text), "text": text}


def _build_insights(df: pd.DataFrame, profile: dict, computed: dict) -> list[str]:
    """
    Diagnostic insights. Every fact that has a driver should name the driver
    (which product, country, salesperson caused the change), not just quote
    the percentage.
    """
    out: list[str] = []
    q = profile["quality"]
    if q["duplicate_rows"]:
        out.append(f"{q['duplicate_rows']} duplicate rows detected — dedup before reporting.")
    for col, pct in q["null_percentage"].items():
        if pct > 30:
            out.append(f"`{col}` is missing in {pct}% of rows.")
            break

    # Diagnose each KPI change — attribute to the top contributor
    date_cols = profile["classification"]["date_columns"]
    dims = profile["classification"]["dimensions"]
    root_by_measure = computed.setdefault("root_cause_by_measure", {})
    primary_dim = dims[0] if dims else None
    date_col = date_cols[0] if date_cols else None

    for kpi in computed.get("kpis", []):
        if kpi.get("change_pct") is None:
            continue
        m = kpi["name"]
        direction = "increased" if kpi["change_pct"] > 0 else "decreased"
        base_line = (
            f"{m} {direction} {abs(kpi['change_pct'])}% "
            f"({kpi.get('compare_previous_period', 'prev')} → "
            f"{kpi.get('compare_current_period', 'latest')})."
        )
        rc = None
        if primary_dim and date_col:
            try:
                from . import analytics
                rc = analytics.root_cause(df, m, primary_dim, date_col=date_col)
                root_by_measure[m] = rc
            except Exception:
                rc = None
        if rc and rc.get("decliners") and kpi["change_pct"] < 0:
            worst = rc["decliners"][0]
            worst_name = worst.get(primary_dim)
            out.append(
                f"{base_line} Largest drag: {worst_name} "
                f"({round(worst.get('delta', 0), 2)})."
            )
        elif rc and rc.get("gainers") and kpi["change_pct"] > 0:
            best = rc["gainers"][0]
            best_name = best.get(primary_dim)
            out.append(
                f"{base_line} Largest contributor: {best_name} "
                f"(+{round(best.get('delta', 0), 2)})."
            )
        else:
            out.append(base_line)

    pareto_data = computed.get("pareto")
    if pareto_data and pareto_data.get("top_80pct_count"):
        items = pareto_data.get("items", [])
        example = ""
        if items:
            names = [str(r.get(pareto_data["dimension"], "")) for r in items[:3]]
            example = f" Top 3: {', '.join(n for n in names if n)}."
        out.append(
            f"Just {pareto_data['top_80pct_count']} {pareto_data['dimension']} values "
            f"drive 80% of {pareto_data['measure']}.{example}"
        )

    corr = computed.get("correlation")
    if corr:
        for pair in corr.get("strong_pairs", [])[:2]:
            sign = "positively" if pair["r"] > 0 else "negatively"
            out.append(
                f"`{pair['a']}` and `{pair['b']}` are {sign} correlated "
                f"(r={pair['r']}, level: {corr.get('level', 'per row')})."
            )

    anoms = computed.get("anomalies")
    if anoms and anoms.get("count"):
        top = (anoms.get("anomalies") or [])[:1]
        example = ""
        if top:
            first = top[0]
            bits = [f"{k}={v}" for k, v in first.items()
                    if k not in {"index", "z"} and v is not None][:3]
            if bits:
                example = f" First: {', '.join(bits)}."
        out.append(f"{anoms['count']} anomalies detected in {anoms['measure']} (|z|>3).{example}")

    return out[:8]


def _build_recommendations(profile: dict, computed: dict) -> list[str]:
    """
    Data-aware recommendations. Every suggestion MUST reference a column that
    actually exists in the current profile. No hardcoded 'pricing / campaigns /
    customer cohorts' — those only appear if columns with those names are
    present.
    """
    recs: list[str] = []
    classification = profile["classification"]
    measures = classification["measures"]
    dimensions = classification["dimensions"]
    date_cols = classification["date_columns"]

    # 1. Pareto → focus on the concentrated few
    pareto_data = computed.get("pareto")
    if pareto_data and pareto_data.get("top_80pct_count"):
        recs.append(
            f"Focus effort on the {pareto_data['top_80pct_count']} "
            f"{pareto_data['dimension']} value(s) that drive 80% of "
            f"{pareto_data['measure']}."
        )

    # 2. KPI declines → diagnostic, using actual column names + attribution
    for kpi in computed.get("kpis", []):
        if kpi.get("change_pct") is None or abs(kpi["change_pct"]) < 5:
            continue
        m = kpi["name"]
        # Attribute using root_cause if available
        rc = computed.get("root_cause_by_measure", {}).get(m)
        if rc and rc.get("decliners") and rc["pct_change"] and rc["pct_change"] < 0:
            worst = rc["decliners"][0]
            dim = rc.get("dimension")
            recs.append(
                f"{m} fell {abs(kpi['change_pct'])}% "
                f"({kpi.get('compare_previous_period')} → {kpi.get('compare_current_period')}). "
                f"Biggest drag: {worst.get(dim)}. Investigate this {dim} first."
            )
        elif rc and rc.get("gainers") and rc["pct_change"] and rc["pct_change"] > 0:
            best = rc["gainers"][0]
            dim = rc.get("dimension")
            recs.append(
                f"{m} rose {kpi['change_pct']}%. Largest contributor was "
                f"{best.get(dim)} — replicate what worked there elsewhere."
            )
        else:
            direction = "decline" if kpi["change_pct"] < 0 else "gain"
            recs.append(
                f"Investigate the {direction} in {m} ({kpi['change_pct']}%) by "
                f"segmenting on {dimensions[0] if dimensions else 'the largest dimension'}."
            )

    # 3. Compare top and bottom performers on the primary dimension
    if measures and dimensions:
        recs.append(
            f"Compare top and bottom {dimensions[0]} on {measures[0]} to see "
            f"which practices are transferable."
        )

    # 4. Time-based drill if a date column exists
    if measures and date_cols:
        recs.append(
            f"Break {measures[0]} down by {date_cols[0]} year/quarter/month to "
            f"spot seasonality."
        )

    # 5. Cross-dimension slicing if multiple dimensions exist
    if measures and len(dimensions) >= 2:
        recs.append(
            f"Slice {measures[0]} by {dimensions[0]} × {dimensions[1]} to find "
            f"pockets of over- or under-performance."
        )

    # 6. Anomaly follow-through — reference the actual measure
    anoms = computed.get("anomalies")
    if anoms and anoms.get("count"):
        recs.append(
            f"Review the {anoms['count']} flagged {anoms.get('measure', '')} "
            f"anomalies to confirm whether they're real events or data errors."
        )

    # Fallback: always give the user something referencing their columns
    if not recs and measures:
        recs.append(
            f"Add a filter on {dimensions[0] if dimensions else 'a dimension'} "
            f"and re-run to isolate the driver."
        )

    # De-dupe while preserving order
    seen = set()
    unique = []
    for r in recs:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique[:6]


def _build_executive_summary(profile: dict, computed: dict) -> str:
    """
    One-paragraph plain-English executive summary composed from computed
    numbers only — never invented text. Safe to render as an opening quote
    on the dashboard.
    """
    sentences: list[str] = []
    domain = profile["domain"]["primary"]
    row_count = profile["quality"]["total_rows"]
    sentences.append(
        f"Analysed {row_count:,} rows of {domain} data across "
        f"{profile['quality'].get('total_columns_source', profile['quality']['total_columns'])} source columns."
    )

    kpis = computed.get("kpis", [])
    for kpi in kpis[:2]:
        if kpi.get("change_pct") is None:
            continue
        direction = "up" if kpi["change_pct"] > 0 else "down"
        sentences.append(
            f"{kpi['name']} was {kpi.get('current_value')} in "
            f"{kpi.get('compare_current_period')}, {direction} "
            f"{abs(kpi['change_pct'])}% ({kpi.get('change_abs'):+}) vs "
            f"{kpi.get('compare_previous_period')}."
        )

    root_map = computed.get("root_cause_by_measure", {})
    for m, rc in list(root_map.items())[:1]:
        if not rc:
            continue
        if rc.get("decliners") and (rc.get("pct_change") or 0) < 0:
            worst = rc["decliners"][0]
            dim = rc.get("dimension")
            sentences.append(
                f"The largest drag on {m} was {worst.get(dim)} "
                f"({round(worst.get('delta', 0), 2)})."
            )
        elif rc.get("gainers") and (rc.get("pct_change") or 0) > 0:
            best = rc["gainers"][0]
            dim = rc.get("dimension")
            sentences.append(
                f"The largest contributor to {m} growth was {best.get(dim)} "
                f"(+{round(best.get('delta', 0), 2)})."
            )

    pareto = computed.get("pareto")
    if pareto and pareto.get("top_80pct_count"):
        sentences.append(
            f"{pareto['top_80pct_count']} of {pareto.get('total_items')} "
            f"{pareto['dimension']} concentrate 80% of {pareto['measure']}."
        )

    anoms = computed.get("anomalies")
    if anoms and anoms.get("count"):
        sentences.append(
            f"{anoms['count']} data points in {anoms['measure']} sit beyond 3σ and warrant review."
        )

    corr = computed.get("correlation")
    if corr and corr.get("strong_pairs"):
        pair = corr["strong_pairs"][0]
        sign = "positively" if pair["r"] > 0 else "negatively"
        sentences.append(
            f"{pair['a']} and {pair['b']} are strongly {sign} related (r={pair['r']})."
        )

    return " ".join(sentences)


def _build_suggested_questions(profile: dict, context: dict | None = None) -> list[str]:
    """
    Generate contextual suggestions. If `context` (last intent + computed
    results) is supplied, produce follow-ups that build on it; otherwise
    fall back to generic starter questions.
    """
    measure = _pick_primary_measure(profile)
    dim = _pick_primary_dimension(profile)
    date = _pick_primary_date(profile)
    dims = profile["classification"]["dimensions"]
    measures = profile["classification"]["measures"]
    suggestions: list[str] = []

    if context:
        op = context.get("op")
        c_measure = context.get("measure") or measure
        c_dim = context.get("dimension") or dim
        if op == "top" and c_measure and c_dim:
            suggestions.append(f"Why is {c_dim} the biggest driver of {c_measure}?")
            suggestions.append(f"Show {c_measure} trend for the top {c_dim}")
            other_dim = next((d for d in dims if d != c_dim), None)
            if other_dim:
                suggestions.append(f"Break {c_measure} down by {other_dim}")
        elif op == "trend" and c_measure and date:
            suggestions.append(f"Forecast {c_measure} for the next 6 months")
            suggestions.append(f"Find anomalies in {c_measure}")
            if c_dim:
                suggestions.append(f"Which {c_dim} drove the change in {c_measure}?")
        elif op == "forecast" and c_measure:
            other_m = next((m for m in measures if m != c_measure), None)
            if other_m:
                suggestions.append(f"Forecast {other_m} for the next 6 months")
            suggestions.append(f"How accurate has {c_measure} forecast been historically?")
        elif op == "anomaly" and c_measure:
            if c_dim:
                suggestions.append(f"Which {c_dim} produced the most anomalies?")
            suggestions.append(f"Trend of {c_measure} around the anomaly dates")
        elif op == "correlation":
            if c_measure and c_dim:
                suggestions.append(f"Show {c_measure} by {c_dim} for the strongest pair")

    # Always-useful fallbacks
    generic = []
    if measure and date:
        generic.append(f"Show {measure} trend by month")
        generic.append(f"Forecast {measure} for the next 6 months")
    if measure and dim:
        generic.append(f"Top 10 {dim} by {measure}")
        generic.append(f"Why did {measure} change recently?")
    generic.append("Find anomalies in the data")
    generic.append("Show correlations between measures")

    # Merge — contextual first, generic to fill, unique
    out = []
    seen = set()
    for s in suggestions + generic:
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= 6:
            break
    return out


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

    # If the dataset has no numeric measures (HR / survey / catalog data),
    # emit count-based KPI cards so the overview isn't empty.
    if not computed["kpis"]:
        total_rows = int(len(df))
        computed["kpis"].append({
            "name": "Total records", "value": total_rows,
            "current_value": total_rows, "trend": "stable",
            "change_pct": None,
        })
        for dim in classified["dimensions"][:3]:
            uniques = int(df[dim].nunique(dropna=True))
            computed["kpis"].append({
                "name": f"Unique {dim}", "value": uniques,
                "current_value": uniques, "trend": "stable",
                "change_pct": None,
            })

    charts: list[dict] = [_kpi_card(k) for k in computed["kpis"]]

    # For each of the top 3 dimensions, add a count-by-value bar chart.
    # This is what makes overview useful for HR / survey data.
    if not measures:
        for dim in classified["dimensions"][:4]:
            ct = analytics.count_by(df, dim, n=10)
            if ct.get("items"):
                charts.append({
                    "id": _id(),
                    "type": "bar",
                    "title": f"Records by {dim}",
                    "x": dim,
                    "y": "count",
                    "data": ct["items"],
                    "why": f"{ct['unique_values']} distinct {dim} values. Bar shows record count per value.",
                    "summary": (
                        f"Top: {ct['items'][0].get(dim)} "
                        f"({ct['items'][0].get('count')} records, "
                        f"{ct['items'][0].get('percentage')}%)."
                    ),
                })

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
        corr = analytics.correlation_matrix(df, measures,
                                            date_col=date_col,
                                            dimension=primary_dim)
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
            "total_columns": profile["quality"].get("total_columns_source", profile["quality"]["total_columns"]),
            "engineered_columns": len(profile.get("engineered_features", [])),
            "engineered_features": profile.get("engineered_features", []),
            "duplicates": profile["quality"]["duplicate_rows"],
            "issues": profile["quality"]["issues"],
        },
        "kpis": computed["kpis"],
        "charts": charts,
        "filters": filters,
        "drilldowns": drilldowns,
        "insights": _build_insights(df, profile, computed),
        "recommendations": _build_recommendations(profile, computed),
        "executive_summary": _build_executive_summary(profile, computed),
        "generated_at": _now_iso(),
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
            "measure": measure,
            "history": f.get("history", []),
            "data": f.get("forecast", []),
            "accuracy": f.get("accuracy"),
            "why": f"Projection {periods} periods ahead based on observed history.",
        })
        query_result_rows = f.get("forecast", [])

    elif op == "top" and dimension and not measure:
        # No numeric measure available (e.g. HR/exit-interview data) —
        # fall back to counting records per dimension value.
        n = int(intent.get("n") or 10)
        ct = analytics.count_by(df, dimension, n=n, ascending=ascending)
        if ct.get("items"):
            direction = "Bottom" if ascending else "Top"
            charts.append({
                "id": _id(),
                "type": "bar",
                "title": f"{direction} {n} {dimension} by record count",
                "x": dimension,
                "y": "count",
                "data": ct["items"],
                "why": (
                    f"Ranked {dimension} by number of records. "
                    f"No numeric measure was found in the dataset, so counts are used."
                ),
                "summary": (
                    f"Top: {ct['items'][0].get(dimension)} "
                    f"({ct['items'][0].get('count')}, {ct['items'][0].get('percentage')}%)."
                ),
            })
            query_result_rows = ct["items"]
            computed["count_result"] = ct

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

    elif op == "count":
        target_dim = dimension or _pick_primary_dimension(profile)
        if target_dim:
            n = int(intent.get("n") or 20)
            ct = analytics.count_by(df, target_dim, n=n, ascending=ascending)
            if ct.get("items"):
                charts.append({
                    "id": _id(),
                    "type": "bar",
                    "title": f"Count by {target_dim}",
                    "x": target_dim,
                    "y": "count",
                    "data": ct["items"],
                    "why": (
                        f"{ct['total']:,} records across {ct['unique_values']} "
                        f"distinct {target_dim} values. Bar shows count per value."
                    ),
                    "summary": (
                        f"Top: {ct['items'][0].get(target_dim)} "
                        f"({ct['items'][0].get('count')}, {ct['items'][0].get('percentage')}%)."
                    ),
                })
                query_result_rows = ct["items"]
                computed["count_result"] = ct
                # KPI card for the total
                total_kpi = {
                    "name": "Total records",
                    "value": ct["total"],
                    "current_value": ct["total"],
                    "trend": "stable",
                    "change_pct": None,
                }
                computed["kpis"] = [total_kpi]
                charts.insert(0, _kpi_card(total_kpi))
            else:
                charts.append(_no_measure_card())
        else:
            charts.append(_no_measure_card())

    elif op == "root_cause":
        target_measure = measure or _pick_primary_measure(profile)
        target_dim = dimension or _pick_primary_dimension(profile)
        if target_measure and target_dim:
            rc = analytics.root_cause(df, target_measure, target_dim, date_col=date_col)
            computed["root_cause"] = rc
            if date_col:
                t = analytics.trend(df, date_col, target_measure)
                if t.get("series"):
                    charts.append(_trend_chart(t))
            direction = ("declined" if (rc.get("pct_change") or 0) < 0
                          else "grew" if (rc.get("pct_change") or 0) > 0 else "was flat")
            charts.append({
                "id": _id(),
                "type": "bar",
                "title": f"Top {target_dim} that DROVE the change ({rc.get('period_prev')} → {rc.get('period_curr')})",
                "x": target_dim,
                "y": "delta",
                "data": rc.get("gainers", []),
                "why": f"Values on top gained most; {target_measure} {direction} "
                       f"{rc.get('pct_change')}% overall.",
            })
            charts.append({
                "id": _id(),
                "type": "bar",
                "title": f"Top {target_dim} that HURT the change",
                "x": target_dim,
                "y": "delta",
                "data": rc.get("decliners", []),
                "why": "Values here fell the most between the two periods.",
            })
            computed["narrative"] = _root_cause_narrative(rc, target_measure, target_dim)
        else:
            charts.append(_no_measure_card())

    elif op == "correlation":
        corr = analytics.correlation_matrix(
            df, profile["classification"]["measures"],
            date_col=date_col, dimension=dimension,
        )
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

    # When parse confidence is low, expose the columns the parser considered
    # so the frontend can offer a manual override picker.
    _all_measures = profile["classification"].get("measures", [])
    _all_dims = profile["classification"].get("dimensions", [])
    _all_dates = profile["classification"].get("date_columns", [])
    parse_hint = None
    if intent.get("confidence", 0) < 0.7 or intent.get("op") in ("summary", "unknown"):
        parse_hint = {
            "reason": "Low parser confidence — pick columns manually if this dashboard isn't what you meant.",
            "picked_measure": intent.get("measure"),
            "picked_dimension": intent.get("dimension"),
            "picked_date": intent.get("date_col"),
            "available_measures": _all_measures,
            "available_dimensions": _all_dims[:20],  # cap for UI
            "available_dates": _all_dates,
        }

    spec = {
        "id": _id(),
        "title": question.strip().rstrip("?") or "Query Result",
        "business_goal": f"Answer: {question}",
        "question": question,
        "intent": intent,
        "parse_hint": parse_hint,
        "generated_at": _now_iso(),
        "kpis": computed.get("kpis", []),
        "charts": charts,
        "result_preview": query_result_rows[:25],
        "filters": [],
        "drilldowns": [],
        "insights": insights,
        "recommendations": recommendations,
        "narrative": computed.get("narrative"),
        "suggested_questions": _build_suggested_questions(profile, context=intent),
    }
    return spec


def _ord_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _root_cause_narrative(rc: dict, measure: str, dimension: str) -> str:
    """
    Compose a paragraph-form executive narrative from the root-cause result.
    Purely template + computed values — no LLM, no invented facts.
    """
    prev_p = rc.get("period_prev", "the prior period")
    curr_p = rc.get("period_curr", "the current period")
    pct = rc.get("pct_change")
    prev_total = rc.get("prev_total")
    curr_total = rc.get("curr_total")
    delta_total = rc.get("delta_total")
    gainers = rc.get("gainers", []) or []
    decliners = rc.get("decliners", []) or []

    if pct is None:
        return (
            f"Unable to compute a period-over-period narrative for {measure} "
            "because the dataset lacks two complete comparison periods."
        )

    direction_word = "declined" if pct < 0 else "grew" if pct > 0 else "was flat"
    magnitude = f"{abs(pct)}%" if pct is not None else ""
    delta_str = (f"{'+' if (delta_total or 0) > 0 else ''}"
                  f"{round(delta_total, 2)}") if delta_total is not None else ""

    parts: list[str] = []

    parts.append(
        f"Between {prev_p} and {curr_p}, {measure} {direction_word} "
        f"{magnitude} ({delta_str}), moving from {prev_total} to {curr_total}."
    )

    if pct != 0:
        if pct < 0 and decliners:
            worst = decliners[0]
            worst_name = worst.get(dimension)
            worst_delta = round(worst.get("delta", 0), 2)
            parts.append(
                f"The single biggest drag on the change was {dimension} "
                f"'{worst_name}', which contributed a swing of {worst_delta} "
                f"({round(worst.get(measure + '_prev', 0), 2)} → "
                f"{round(worst.get(measure + '_curr', 0), 2)})."
            )
            if len(decliners) >= 2:
                names = ", ".join(f"'{d.get(dimension)}'" for d in decliners[:3]
                                    if d.get(dimension))
                parts.append(
                    f"Other {dimension} values with notable declines include {names}. "
                    f"Investigating these three first is likely the highest-impact use of time."
                )
        elif pct > 0 and gainers:
            best = gainers[0]
            best_name = best.get(dimension)
            best_delta = round(best.get("delta", 0), 2)
            parts.append(
                f"The largest contributor to the gain was {dimension} '{best_name}', "
                f"which added {best_delta} to {measure} "
                f"({round(best.get(measure + '_prev', 0), 2)} → "
                f"{round(best.get(measure + '_curr', 0), 2)})."
            )
            if len(gainers) >= 2:
                names = ", ".join(f"'{d.get(dimension)}'" for d in gainers[:3]
                                    if d.get(dimension))
                parts.append(
                    f"Other strong contributors include {names}. Understanding "
                    f"what worked here can inform where to double down."
                )

    if gainers and decliners and pct is not None:
        top_gain = gainers[0].get("delta", 0) or 0
        top_drag = decliners[0].get("delta", 0) or 0
        if abs(top_gain) > abs(delta_total or 0) * 0.5 or abs(top_drag) > abs(delta_total or 0) * 0.5:
            parts.append(
                f"The overall movement is concentrated in a small number of "
                f"{dimension} values rather than being spread evenly — a targeted "
                f"intervention will therefore likely outperform broad changes."
            )

    return " ".join(parts)


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

    elif op == "root_cause":
        rc = computed.get("root_cause", {})
        if rc.get("pct_change") is not None:
            direction = "declined" if rc["pct_change"] < 0 else "grew"
            out.append(
                f"{measure or 'The measure'} {direction} {abs(rc['pct_change'])}% "
                f"from {rc['period_prev']} to {rc['period_curr']}."
            )
        gainers = rc.get("gainers", [])
        decliners = rc.get("decliners", [])
        dim = rc.get("dimension") or dimension
        if gainers:
            top = gainers[0]
            out.append(
                f"Biggest positive contributor: {top.get(dim)} "
                f"(+{round(top.get('delta', 0), 2)})."
            )
        if decliners:
            worst = decliners[0]
            out.append(
                f"Biggest drag: {worst.get(dim)} "
                f"({round(worst.get('delta', 0), 2)})."
            )

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
    """
    Query-specific recommendations. Every string references only fields that
    actually exist in the current dataset — no invented "pricing / channels /
    cohorts" concepts.
    """
    op = intent.get("op")
    measure = intent.get("measure")
    dimension = intent.get("dimension")
    date_col = intent.get("date_col") or (
        profile["classification"]["date_columns"][0]
        if profile["classification"]["date_columns"] else None
    )
    recs: list[str] = []

    if op == "top" and rows and dimension and measure:
        if intent.get("ascending"):
            worst = rows[0].get(dimension)
            recs.append(
                f"Investigate {worst}: it has the lowest {measure}. "
                f"Compare against the top {dimension} to identify what's different."
            )
        else:
            best = rows[0].get(dimension)
            recs.append(
                f"Study {best}: it leads on {measure}. Look at what makes it work "
                f"and test replicating in other {dimension}s."
            )
    elif op == "forecast":
        recs.append(
            f"Track the forecast weekly against actual {measure or 'values'}; "
            f"if variance exceeds the reported MAPE, revisit the model."
        )
    elif op == "anomaly" and rows:
        example = rows[0]
        example_bits = ", ".join(
            f"{k}={v}" for k, v in example.items()
            if k not in {"index", "z"} and v is not None
        )
        recs.append(
            f"Start with the first flagged row ({example_bits}) — confirm it's real "
            f"before broader changes."
        )
    elif op == "correlation":
        if measure and dimension:
            recs.append(
                f"Aggregate {measure} by {dimension} before drawing conclusions — "
                f"row-level correlation can hide relationships."
            )
    elif op == "trend" and measure and date_col:
        recs.append(
            f"Overlay a moving average on {measure} over {date_col} to smooth "
            f"noise and confirm the direction."
        )

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
