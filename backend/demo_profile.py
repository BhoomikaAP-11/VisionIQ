"""
Stand-alone CLI that profiles a single Excel/CSV file end-to-end and prints
the resulting dashboard spec as JSON. Used by run_demo.ps1 to show the
pipeline working on the user's uploaded file without spinning up the API.

Usage:
    python -m backend.demo_profile path\to\file.xlsx
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(path: str) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.services import dashboard, excel_service

    p = Path(path)
    if not p.exists():
        print(f"File not found: {path}")
        return 1

    print(f"\n=== Profiling {p.name} ===")
    data = excel_service.read_file(str(p))
    profile = data["profile"]
    print(f"Sheets: {profile['sheet_count']}, total rows: {profile['total_rows']}")
    print(f"Primary sheet: {profile['primary_sheet']}")

    for name, sheet_profile in profile["sheets"].items():
        print(f"\n--- Sheet: {name} ---")
        print(f"  {sheet_profile['row_count']} rows x {sheet_profile['column_count']} columns")
        print(f"  Domain: {sheet_profile['domain']['primary']} "
              f"(confidence {sheet_profile['domain']['confidence']:.2f})")
        print(f"  Quality score: {sheet_profile['quality']['quality_score']}/100")
        print(f"  Measures: {sheet_profile['classification']['measures']}")
        print(f"  Dimensions: {sheet_profile['classification']['dimensions']}")
        print(f"  Date columns: {sheet_profile['classification']['date_columns']}")
        if sheet_profile['quality']['issues']:
            print(f"  Issues: {sheet_profile['quality']['issues']}")

    # Build the overview dashboard for the primary sheet
    primary = profile["primary_sheet"]
    if primary:
        df = data["sheets"][primary]
        spec = dashboard.build_executive_overview(df, profile["sheets"][primary])
        print(f"\n=== Executive Overview ({primary}) ===")
        print(f"  Business goal: {spec['business_goal']}")
        print(f"  {len(spec['kpis'])} KPIs, {len(spec['charts'])} charts")
        print("\n  Insights:")
        for ins in spec["insights"]:
            print(f"    - {ins}")
        print("\n  Recommendations:")
        for rec in spec["recommendations"]:
            print(f"    - {rec}")
        print("\n  Suggested questions:")
        for q in spec["suggested_questions"]:
            print(f"    - {q}")

        out_path = Path("backend") / "uploads" / f"{primary}_dashboard.json"
        out_path.write_text(json.dumps(spec, indent=2, default=str), encoding="utf-8")
        print(f"\nFull dashboard JSON written to {out_path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m backend.demo_profile <file.xlsx>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
