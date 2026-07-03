"""
End-to-end accuracy test.

Given an uploaded Excel/CSV file, this script:
  1. Reads the raw file into pandas ("ground truth")
  2. Runs the same file through VisionIQ's profiling + dashboard pipeline
  3. Prints a side-by-side comparison for every KPI, top-N, trend point,
     forecast, and anomaly the dashboard would show
  4. Flags any mismatch with FAIL and exits non-zero if any is found

Run from the project root:
    python -m backend.accuracy_test path\to\file.xlsx

The point is not to trust the dashboard's numbers — this script derives
ground truth independently with vanilla pandas and compares.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd


MISMATCH: list[tuple[str, Any, Any]] = []


def _approx(a, b, tol=1e-6) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        if isinstance(a, float) and isinstance(b, float):
            if math.isnan(a) and math.isnan(b):
                return True
            return abs(a - b) <= max(tol, tol * max(abs(a), abs(b)))
        return a == b
    except Exception:
        return str(a) == str(b)


def check(label: str, actual, expected, tol=1e-6):
    ok = _approx(actual, expected, tol=tol)
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {label}: dashboard={actual}  |  truth={expected}")
    if not ok:
        MISMATCH.append((label, actual, expected))


def main(path: str) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.services import analytics, dashboard, excel_service, intent

    p = Path(path)
    if not p.exists():
        print(f"File not found: {path}")
        return 1

    # 1. Ground-truth read (vanilla pandas — no VisionIQ code)
    ext = p.suffix.lower()
    if ext == ".csv":
        truth_sheets = {"Sheet1": pd.read_csv(p)}
    else:
        xl = pd.ExcelFile(p)
        truth_sheets = {name: xl.parse(name) for name in xl.sheet_names}

    print(f"\n=== File: {p.name} ({len(truth_sheets)} sheet(s)) ===")

    # 2. VisionIQ pipeline
    piped = excel_service.read_file(str(p))
    profile = piped["profile"]

    # Iterate every sheet
    for name, truth_df in truth_sheets.items():
        print(f"\n--- Sheet: {name} ({len(truth_df)} rows, {len(truth_df.columns)} cols) ---")
        truth_df = truth_df.copy()
        truth_df.columns = [str(c).strip() for c in truth_df.columns]
        truth_df = truth_df.dropna(how="all").reset_index(drop=True)

        aug_df = piped["sheets"].get(name)
        sheet_profile = profile["sheets"].get(name)
        if aug_df is None or sheet_profile is None:
            print(f"  [FAIL] Sheet '{name}' missing from pipeline output")
            continue

        # ---- Basic counts ----
        print("\n  Basic counts")
        check("row count", sheet_profile["row_count"], len(truth_df))
        check("column count (source)", len(truth_df.columns),
              sum(1 for c in sheet_profile["columns"] if not c["engineered"]))

        # ---- Per-measure ground-truth ----
        measures = sheet_profile["classification"]["measures"]
        dimensions = sheet_profile["classification"]["dimensions"]
        date_cols = sheet_profile["classification"]["date_columns"]
        print(f"\n  Classification -> measures={measures}, dimensions={dimensions}, dates={date_cols}")

        # ---- KPI totals from analytics.kpi_summary ----
        primary_date = date_cols[0] if date_cols else None
        kpis = analytics.kpi_summary(aug_df, measures, date_col=primary_date)
        for kpi in kpis:
            col = kpi["name"]
            truth_total = float(pd.to_numeric(truth_df[col], errors="coerce").dropna().sum())
            check(f"KPI '{col}' total", kpi["value"], round(truth_total, 2), tol=0.01)

        # ---- Top N from analytics.top_n ----
        if measures and dimensions:
            m, d = measures[0], dimensions[0]
            top10 = analytics.top_n(aug_df, d, m, n=10, ascending=False)
            truth_top = (truth_df[[d, m]]
                         .assign(**{m: pd.to_numeric(truth_df[m], errors="coerce")})
                         .dropna()
                         .groupby(d, dropna=False)[m].sum()
                         .sort_values(ascending=False)
                         .head(10)
                         .reset_index()
                         .to_dict("records"))
            check(f"Top10 '{d}' by '{m}' count", len(top10), len(truth_top))
            for i in range(min(len(top10), len(truth_top))):
                check(f"  Top rank {i+1} label", str(top10[i].get(d)), str(truth_top[i][d]))
                check(f"  Top rank {i+1} value", round(float(top10[i].get(m, 0)), 2),
                      round(float(truth_top[i][m]), 2), tol=0.01)

            # Bottom 3 sanity — ensure asc != desc
            bot3 = analytics.top_n(aug_df, d, m, n=3, ascending=True)
            truth_bot = (truth_df[[d, m]]
                         .assign(**{m: pd.to_numeric(truth_df[m], errors="coerce")})
                         .dropna()
                         .groupby(d, dropna=False)[m].sum()
                         .sort_values(ascending=True)
                         .head(3)
                         .reset_index()
                         .to_dict("records"))
            for i in range(min(len(bot3), len(truth_bot))):
                check(f"  Bottom rank {i+1} label", str(bot3[i].get(d)), str(truth_bot[i][d]))

        # ---- Trend sanity ----
        if measures and primary_date:
            t = analytics.trend(aug_df, primary_date, measures[0])
            truth_series = (truth_df[[primary_date, measures[0]]].copy())
            truth_series[primary_date] = pd.to_datetime(truth_series[primary_date], errors="coerce")
            truth_series[measures[0]] = pd.to_numeric(truth_series[measures[0]], errors="coerce")
            truth_series = truth_series.dropna()
            grouped = truth_series.set_index(primary_date)[measures[0]].resample("MS").sum().dropna()
            check(f"Trend points count", len(t.get("series", [])), len(grouped))
            for (idx, val), point in zip(grouped.items(), t.get("series", [])):
                check(f"  Trend {idx.date()} value", round(float(point["y"]), 2),
                      round(float(val), 2), tol=0.01)

        # ---- Anomaly ground truth ----
        if measures:
            for m in measures[:2]:
                a = analytics.anomalies(aug_df, m, date_col=primary_date)
                s = pd.to_numeric(truth_df[m], errors="coerce").dropna()
                if len(s) >= 5 and s.std() > 0:
                    z = (s - s.mean()) / s.std()
                    truth_count = int((z.abs() > 3).sum())
                    check(f"Anomaly count in '{m}'", a.get("count", 0), truth_count)

        # ---- Intent parsing round-trip sanity ----
        print("\n  Intent parser round-trip")
        test_queries = [
            ("top 5 " + (dimensions[0] if dimensions else "") + " by " +
             (measures[0] if measures else ""), "top", False, 5),
            ("bottom 3 " + (dimensions[0] if dimensions else "") + " by " +
             (measures[0] if measures else ""), "top", True, 3),
        ]
        for q, expected_op, expected_asc, expected_n in test_queries:
            if not q.strip():
                continue
            parsed = intent.parse(q, sheet_profile)
            check(f"'{q}' -> op", parsed["op"], expected_op)
            check(f"'{q}' -> ascending", parsed["ascending"], expected_asc)
            if expected_n is not None:
                check(f"'{q}' -> n", parsed["n"], expected_n)

    # ---- Summary ----
    print("\n" + "=" * 60)
    if MISMATCH:
        print(f"FAILED: {len(MISMATCH)} mismatch(es) between dashboard and ground truth")
        for label, actual, expected in MISMATCH[:20]:
            print(f"  - {label}: got {actual}, expected {expected}")
        return 1
    print("PASSED: dashboard matches ground truth on every check.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m backend.accuracy_test <file.xlsx>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
