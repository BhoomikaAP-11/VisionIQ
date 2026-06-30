"""
Standalone smoke test — verifies the profiling + dashboard pipeline end-to-end
without spinning up the HTTP server. Run from the project root:

    python -m backend.smoke_test

Exits with code 0 on success, 1 on the first failure. Touches no network.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


def _build_fixture() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    days = 365
    start = datetime(2024, 1, 1)
    dates = [start + timedelta(days=i) for i in range(days)]
    regions = rng.choice(["North", "South", "East", "West"], days)
    categories = rng.choice(["Electronics", "Apparel", "Home", "Sports"], days)
    revenue = (rng.normal(1000, 250, days).clip(0) + np.linspace(0, 400, days)).round(2)
    profit = (revenue * rng.uniform(0.1, 0.3, days)).round(2)
    units = rng.integers(1, 50, days)
    return pd.DataFrame({
        "OrderDate": dates,
        "Region": regions,
        "Category": categories,
        "Revenue": revenue,
        "Profit": profit,
        "Units": units,
    })


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.services import dashboard, profiling

    df = _build_fixture()
    print(f"[1/4] Fixture built: {len(df)} rows, {df.columns.tolist()}")

    profile, augmented = profiling.profile_dataframe(df, name="orders")
    assert profile["row_count"] == len(df), "row count mismatch"
    assert "OrderDate__Year" in augmented.columns, "engineered features missing"
    assert "Revenue" in profile["classification"]["measures"], "Revenue should be a measure"
    assert "Region" in profile["classification"]["dimensions"], "Region should be a dimension"
    assert "OrderDate" in profile["classification"]["date_columns"], "OrderDate should be detected"
    assert profile["domain"]["primary"] in {"sales", "retail"}, f"domain was {profile['domain']}"
    print(f"[2/4] Profile OK — domain={profile['domain']['primary']}, "
          f"quality={profile['quality']['quality_score']}")

    spec = dashboard.build_executive_overview(augmented, profile)
    assert spec["title"] == "Executive Overview"
    assert len(spec["kpis"]) > 0, "no KPIs generated"
    assert len(spec["charts"]) > 0, "no charts generated"
    assert spec["insights"], "no insights generated"
    print(f"[3/4] Overview OK — {len(spec['kpis'])} KPIs, {len(spec['charts'])} charts, "
          f"{len(spec['insights'])} insights")

    q = dashboard.build_query_dashboard(augmented, profile, "Show me top 5 regions by revenue")
    assert q["intent"]["op"] == "top", f"expected top intent, got {q['intent']}"
    assert any(c["type"] == "bar" for c in q["charts"]), "expected a bar chart"
    print(f"[4/4] Query OK — intent={q['intent']}, charts={len(q['charts'])}")

    # JSON-serialisability check
    json.dumps(spec)
    json.dumps(q)
    print("\nSmoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
