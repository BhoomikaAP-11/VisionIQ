"""
Data profiling, semantic typing, and feature engineering.

Covers Master Prompt Phases 1-3:
- Phase 1: ingestion metadata, data-quality, missing/duplicate/outlier detection
- Phase 2: semantic typing, fact/dimension inference, business-domain detection
- Phase 3: automatic feature engineering on date/numeric columns

The output is a JSON-safe "profile" dict consumed by the dashboard engine.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# JSON-safe coercion
# ---------------------------------------------------------------------------
def safe_val(v: Any):
    """Coerce numpy/pandas scalars into JSON-safe Python primitives."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (pd.Timestamp,)):
        return None if pd.isna(v) else v.isoformat()
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def records(df: pd.DataFrame, limit: int | None = None) -> list[dict]:
    if limit is not None:
        df = df.head(limit)
    return [{k: safe_val(v) for k, v in row.items()} for row in df.to_dict("records")]


# ---------------------------------------------------------------------------
# Semantic typing — go beyond pandas dtypes
# ---------------------------------------------------------------------------
_CURRENCY_HINTS = re.compile(
    r"(revenue|sales|price|amount|total|cost|profit|margin|income|expense|"
    r"spend|gmv|aov|cash|fee|payment|salary|wage|budget|usd|eur|inr|gbp|"
    r"\$|€|£|₹)",
    re.IGNORECASE,
)
_PERCENT_HINTS = re.compile(r"(percent|percentage|pct|rate|%|ratio|share)", re.IGNORECASE)
_ID_HINTS = re.compile(r"(^id$|_id$|^id_|uuid|guid|code|number|no\.?$)", re.IGNORECASE)
_DATE_HINTS = re.compile(r"(date|time|year|month|quarter|week|day|created|updated)", re.IGNORECASE)
_GEO_HINTS = re.compile(
    r"(country|state|city|region|province|zone|territory|district|zip|postal|"
    r"latitude|longitude|address|location)",
    re.IGNORECASE,
)
_CATEGORY_HINTS = re.compile(
    r"(category|type|segment|group|class|status|tier|channel|department|"
    r"product|brand|gender|industry)",
    re.IGNORECASE,
)


def _try_parse_dates(series: pd.Series) -> pd.Series | None:
    """Return a parsed datetime series if at least 70% of non-null values parse."""
    if series.dtype == "object" or pd.api.types.is_string_dtype(series):
        non_null = series.dropna()
        if len(non_null) == 0:
            return None
        try:
            parsed = pd.to_datetime(non_null, errors="coerce", utc=False)
            if parsed.notna().sum() / len(non_null) >= 0.7:
                return pd.to_datetime(series, errors="coerce")
        except Exception:
            return None
    return None


def detect_semantic_type(name: str, series: pd.Series) -> str:
    """
    Classify a column by its business meaning, not just dtype.
    Returns one of: id, date, currency, percentage, numeric, geo,
    category, boolean, text.
    """
    n = str(name).lower()

    if pd.api.types.is_bool_dtype(series):
        return "boolean"

    if _DATE_HINTS.search(n) or pd.api.types.is_datetime64_any_dtype(series):
        return "date"

    if pd.api.types.is_numeric_dtype(series):
        if _ID_HINTS.search(n) and series.nunique(dropna=True) == series.notna().sum():
            return "id"
        if _PERCENT_HINTS.search(n):
            return "percentage"
        if _CURRENCY_HINTS.search(n):
            return "currency"
        return "numeric"

    # object / string columns
    if _GEO_HINTS.search(n):
        return "geo"
    if _ID_HINTS.search(n):
        return "id"

    non_null = series.dropna()
    if len(non_null) > 0:
        unique_ratio = series.nunique(dropna=True) / len(non_null)
        if unique_ratio < 0.5 or _CATEGORY_HINTS.search(n):
            return "category"
    return "text"


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
def detect_outliers_iqr(series: pd.Series) -> dict:
    """Tukey fence outlier count for a numeric series."""
    s = series.dropna()
    if len(s) < 4:
        return {"count": 0, "lower": None, "upper": None}
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    count = int(((s < lower) | (s > upper)).sum())
    return {"count": count, "lower": safe_val(lower), "upper": safe_val(upper)}


def quality_report(df: pd.DataFrame, semantic_types: dict[str, str]) -> dict:
    total = len(df)
    null_counts = {c: int(df[c].isna().sum()) for c in df.columns}
    duplicates = int(df.duplicated().sum())

    outliers = {}
    for col, sem in semantic_types.items():
        if sem in {"numeric", "currency", "percentage"}:
            outliers[col] = detect_outliers_iqr(df[col])

    quality_score = 100.0
    if total > 0:
        null_penalty = sum(null_counts.values()) / (total * max(len(df.columns), 1)) * 40
        dup_penalty = duplicates / total * 30
        quality_score = max(0.0, round(100 - null_penalty - dup_penalty, 1))

    issues = []
    if duplicates > 0:
        issues.append(f"{duplicates} duplicate rows detected")
    high_null_cols = [c for c, n in null_counts.items() if total > 0 and n / total > 0.3]
    if high_null_cols:
        issues.append(f"High null rate (>30%) in: {', '.join(high_null_cols[:5])}")
    heavy_outliers = [c for c, o in outliers.items() if total > 0 and o["count"] / total > 0.1]
    if heavy_outliers:
        issues.append(f"Heavy outliers (>10%) in: {', '.join(heavy_outliers[:5])}")

    return {
        "total_rows": int(total),
        "total_columns": int(len(df.columns)),
        "duplicate_rows": duplicates,
        "null_counts": null_counts,
        "null_percentage": {
            c: round(n / total * 100, 2) if total else 0.0 for c, n in null_counts.items()
        },
        "outliers": outliers,
        "quality_score": quality_score,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Phase 3 — feature engineering
# ---------------------------------------------------------------------------
def add_date_features(df: pd.DataFrame, date_cols: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Add Year/Quarter/Month/etc derived columns. Returns (df, list of new cols)."""
    new_cols: list[str] = []
    for col in date_cols:
        if col not in df.columns:
            continue
        s = pd.to_datetime(df[col], errors="coerce")
        if s.notna().sum() == 0:
            continue
        df[col] = s
        prefix = col
        feats = {
            f"{prefix}__Year": s.dt.year,
            f"{prefix}__Quarter": s.dt.quarter,
            f"{prefix}__Month": s.dt.month,
            f"{prefix}__MonthName": s.dt.strftime("%b"),
            f"{prefix}__Week": s.dt.isocalendar().week.astype("Int64"),
            f"{prefix}__Weekday": s.dt.day_name(),
            f"{prefix}__YearMonth": s.dt.strftime("%Y-%m"),
        }
        for name, values in feats.items():
            if name not in df.columns:
                df[name] = values
                new_cols.append(name)
    return df, new_cols


# ---------------------------------------------------------------------------
# Phase 2 — business-domain inference & fact/dimension classification
# ---------------------------------------------------------------------------
_DOMAIN_KEYWORDS = {
    "sales": ["sale", "revenue", "order", "customer", "discount", "invoice"],
    "finance": ["profit", "loss", "margin", "balance", "ledger", "expense", "tax", "asset"],
    "retail": ["sku", "store", "inventory", "stock", "product", "category"],
    "healthcare": ["patient", "diagnosis", "treatment", "doctor", "hospital", "medication"],
    "hr": ["employee", "salary", "department", "hire", "attrition", "leave"],
    "education": ["student", "course", "grade", "enrollment", "teacher", "school"],
    "banking": ["account", "deposit", "loan", "credit", "branch", "transaction"],
    "insurance": ["policy", "claim", "premium", "coverage", "insured"],
    "manufacturing": ["factory", "production", "defect", "machine", "shift", "yield"],
    "marketing": ["campaign", "lead", "click", "impression", "conversion", "channel"],
    "logistics": ["shipment", "delivery", "warehouse", "carrier", "route", "tracking"],
    "telecom": ["call", "subscriber", "usage", "minutes", "bandwidth", "plan"],
}


def infer_domain(columns: list[str]) -> dict:
    """Return the most likely business domain plus runner-up scores."""
    text = " ".join(c.lower() for c in columns)
    scores = {
        domain: sum(1 for kw in kws if kw in text)
        for domain, kws in _DOMAIN_KEYWORDS.items()
    }
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top, top_score = ranked[0]
    return {
        "primary": top if top_score > 0 else "general",
        "confidence": min(1.0, top_score / 4),
        "scores": {k: v for k, v in ranked if v > 0},
    }


def classify_columns(df: pd.DataFrame, semantic_types: dict[str, str]) -> dict:
    """Split into measures (numeric to aggregate) vs dimensions (group-by)."""
    measures, dimensions, dates, ids = [], [], [], []
    for col, sem in semantic_types.items():
        if sem in {"currency", "percentage", "numeric"}:
            # treat low-cardinality numerics like year as dimensions, not measures
            if df[col].nunique(dropna=True) <= 30 and col.lower() in {"year", "month", "quarter"}:
                dimensions.append(col)
            else:
                measures.append(col)
        elif sem == "date":
            dates.append(col)
        elif sem == "id":
            ids.append(col)
        elif sem in {"category", "geo", "boolean", "text"}:
            if df[col].nunique(dropna=True) <= max(50, int(len(df) * 0.1)):
                dimensions.append(col)
    return {
        "measures": measures,
        "dimensions": dimensions,
        "date_columns": dates,
        "id_columns": ids,
    }


# ---------------------------------------------------------------------------
# Top-level profile
# ---------------------------------------------------------------------------
def profile_dataframe(df: pd.DataFrame, name: str = "Sheet1") -> tuple[dict, pd.DataFrame]:
    """
    Run the full Phase 1-3 pipeline on a single dataframe.
    Returns (profile_dict, augmented_dataframe). The augmented frame includes
    engineered date features and parsed datetimes; callers should store IT,
    not the original, so downstream queries can use the new columns.
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all").reset_index(drop=True)

    # Try parsing string columns that look like dates
    for col in list(df.columns):
        if df[col].dtype == "object":
            parsed = _try_parse_dates(df[col])
            if parsed is not None:
                df[col] = parsed

    semantic_types = {col: detect_semantic_type(col, df[col]) for col in df.columns}
    classified = classify_columns(df, semantic_types)
    df, new_features = add_date_features(df, classified["date_columns"])

    # Re-classify newly engineered columns
    for c in new_features:
        semantic_types[c] = detect_semantic_type(c, df[c])

    quality = quality_report(df, semantic_types)
    domain = infer_domain(list(df.columns))

    columns_meta = []
    for col in df.columns:
        s = df[col]
        non_null = s.dropna()
        meta = {
            "name": col,
            "dtype": str(s.dtype),
            "semantic_type": semantic_types[col],
            "nullable": bool(s.isna().any()),
            "null_count": int(s.isna().sum()),
            "unique_count": int(s.nunique(dropna=True)),
            "sample_values": [safe_val(v) for v in non_null.head(5).tolist()],
            "engineered": col in new_features,
        }
        if semantic_types[col] in {"numeric", "currency", "percentage"} and len(non_null):
            meta["stats"] = {
                "min": safe_val(non_null.min()),
                "max": safe_val(non_null.max()),
                "mean": safe_val(non_null.mean()),
                "median": safe_val(non_null.median()),
                "std": safe_val(non_null.std()),
                "sum": safe_val(non_null.sum()),
            }
        columns_meta.append(meta)

    profile = {
        "name": name,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "columns": columns_meta,
        "semantic_types": semantic_types,
        "classification": classified,
        "engineered_features": new_features,
        "quality": quality,
        "domain": domain,
        "preview": records(df, 100),
    }
    return profile, df


def profile_workbook(sheets: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    Profile every sheet/table and return (augmented_sheets, workbook_summary).
    Use the returned dataframes — they include the engineered features.
    """
    augmented: dict[str, pd.DataFrame] = {}
    profiles: dict[str, dict] = {}
    for name, df in sheets.items():
        profile, aug = profile_dataframe(df, name=name)
        augmented[name] = aug
        profiles[name] = profile
    summary = {
        "sheet_count": len(profiles),
        "total_rows": sum(p["row_count"] for p in profiles.values()),
        "sheets": profiles,
        "primary_sheet": max(profiles, key=lambda n: profiles[n]["row_count"]) if profiles else None,
    }
    return augmented, summary


def build_schema_context(profile: dict) -> str:
    """Render a profile into a compact text block for LLM prompts."""
    if "sheets" in profile:
        parts = []
        for name, p in profile["sheets"].items():
            parts.append(_render_single(p))
        return "\n\n".join(parts)
    return _render_single(profile)


def _render_single(p: dict) -> str:
    lines = [
        f"Table: {p['name']}",
        f"  Rows: {p['row_count']}, Columns: {p['column_count']}",
        f"  Business domain: {p['domain']['primary']} (conf {p['domain']['confidence']:.2f})",
        f"  Quality score: {p['quality']['quality_score']}/100",
        "  Columns:",
    ]
    for c in p["columns"]:
        samples = ", ".join(str(v) for v in c["sample_values"][:3])
        lines.append(f"    - {c['name']} :: {c['semantic_type']} ({c['dtype']}) — e.g. {samples}")
    return "\n".join(lines)
