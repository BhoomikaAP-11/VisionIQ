"""
Excel and CSV file processing service.

Thin I/O wrapper around the profiling engine. Reads the raw bytes into
pandas DataFrames, then hands off to `profiling.profile_workbook` for all
typing, quality, and feature-engineering work.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd

from . import profiling


# Backwards-compatible helpers kept here so existing callers/imports work.
def _safe_val(v: Any):
    return profiling.safe_val(v)


def df_to_records(df: pd.DataFrame) -> list[dict]:
    return profiling.records(df)


def read_sheets(filepath: str) -> dict[str, pd.DataFrame]:
    """Read a file into a dict of sheet_name -> DataFrame."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".csv":
        df = pd.read_csv(filepath)
        df.columns = [str(c).strip() for c in df.columns]
        return {"Sheet1": df}
    if ext in {".xlsx", ".xls", ".xlsm"}:
        xl = pd.ExcelFile(filepath)
        sheets: dict[str, pd.DataFrame] = {}
        for name in xl.sheet_names:
            df = xl.parse(name)
            df.columns = [str(c).strip() for c in df.columns]
            sheets[name] = df
        return sheets
    raise ValueError(f"Unsupported file extension: {ext}")


def read_file(filepath: str) -> dict:
    """
    Full ingestion: read + profile. Returns
    {"sheets": {name: DataFrame}, "profile": workbook_profile}.
    The DataFrames stay in memory for the session store; callers should
    NOT JSON-serialise them directly.
    """
    sheets = read_sheets(filepath)
    augmented_sheets, workbook_profile = profiling.profile_workbook(sheets)
    return {"sheets": augmented_sheets, "profile": workbook_profile}


def build_schema_context(file_data: dict) -> str:
    """Compatibility shim — accepts either the new {profile} or old shape."""
    if "profile" in file_data:
        return profiling.build_schema_context(file_data["profile"])
    # legacy fallback
    lines = []
    for sheet_name, schema in file_data.get("schema", {}).items():
        lines.append(f"Table: {sheet_name}")
        for col in schema.get("columns", []):
            samples = ", ".join(str(v) for v in col.get("sample_values", [])[:3])
            lines.append(f"  - {col['name']} ({col['dtype']}) — samples: {samples}")
        lines.append("")
    return "\n".join(lines)


def run_query_on_df(df: pd.DataFrame, filters: dict | None = None) -> list[dict]:
    """Apply simple substring filters and return records."""
    if filters:
        for col, val in filters.items():
            if col in df.columns:
                df = df[df[col].astype(str).str.contains(str(val), case=False, na=False)]
    return profiling.records(df)


def aggregate_df(df: pd.DataFrame, group_by: str, agg_col: str, agg_func: str = "sum") -> list[dict]:
    if group_by not in df.columns or agg_col not in df.columns:
        return []
    grouped = df.groupby(group_by)[agg_col].agg(agg_func).reset_index()
    grouped.columns = [group_by, f"{agg_func}_{agg_col}"]
    return profiling.records(grouped.sort_values(f"{agg_func}_{agg_col}", ascending=False))
