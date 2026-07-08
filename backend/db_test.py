"""
End-to-end database connectivity test.

What it does:
  1. Creates a small SQLite database (`test_sales.db`) with realistic sales
     data — no external DB server needed.
  2. Calls every DB endpoint on a running VisionIQ backend:
        POST /api/db/connect         -> open connection
        POST /api/db/{id}/load       -> profile a table
        GET  /api/dashboard/{id}/overview  -> dashboard on live DB data
        POST /api/db/{id}/query      -> vetted SELECT
        DELETE /api/db/{id}          -> close
  3. Prints PASS / FAIL for each step and exits non-zero on any failure.

Usage:
    # In terminal 1
    uvicorn backend.main:app --reload --port 8000

    # In terminal 2 (project root)
    python -m backend.db_test

Optional args:
    python -m backend.db_test --base http://localhost:8000
    python -m backend.db_test --keep-db     # don't delete the sqlite file
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import httpx
except ImportError:
    print("Missing dependency 'httpx'. Install with: pip install httpx")
    sys.exit(2)


FAILS: list[str] = []


def check(label: str, condition: bool, detail: str = ""):
    tag = "PASS" if condition else "FAIL"
    print(f"  [{tag}] {label}" + (f"  — {detail}" if detail else ""))
    if not condition:
        FAILS.append(label)


def _create_sqlite(path: Path):
    """Build a small but realistic sales database."""
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY,
            order_date TEXT NOT NULL,
            country TEXT NOT NULL,
            product TEXT NOT NULL,
            sales_person TEXT NOT NULL,
            amount REAL NOT NULL,
            boxes_shipped INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE regions (
            id INTEGER PRIMARY KEY,
            country TEXT UNIQUE NOT NULL,
            continent TEXT NOT NULL
        )
    """)

    countries = ["India", "USA", "UK", "Germany", "Japan", "Australia"]
    continents = {
        "India": "Asia", "USA": "North America", "UK": "Europe",
        "Germany": "Europe", "Japan": "Asia", "Australia": "Oceania",
    }
    products = ["50% Dark Bites", "Peanut Butter Cubes", "99% Dark & Pure",
                "Organic Choco Syrup", "Mint Chip Choco", "White Choc",
                "Manuka Honey Choco", "Fruit & Nut Bars"]
    people = ["Alice", "Bob", "Carol", "Dan", "Eva", "Frank", "Grace", "Hank"]

    for c in countries:
        cur.execute("INSERT INTO regions(country, continent) VALUES(?, ?)", (c, continents[c]))

    # 300 sales rows across Jan–Aug 2024
    import random
    rng = random.Random(42)
    start = datetime(2024, 1, 1)
    for i in range(300):
        d = start + timedelta(days=rng.randint(0, 240))
        cur.execute(
            "INSERT INTO sales(order_date, country, product, sales_person, amount, boxes_shipped) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                d.strftime("%Y-%m-%d"),
                rng.choice(countries),
                rng.choice(products),
                rng.choice(people),
                round(rng.uniform(500, 15000), 2),
                rng.randint(10, 500),
            ),
        )
    conn.commit()
    conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000",
                         help="Backend base URL (default: http://localhost:8000)")
    parser.add_argument("--keep-db", action="store_true",
                         help="Don't delete the SQLite file after the test")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    db_path = Path("backend/uploads/test_sales.db").resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n=== VisionIQ DB connectivity test ===")
    print(f"Backend: {base}")
    print(f"SQLite fixture: {db_path}")

    # ------------------------------------------------------------------
    # Step 0. Health check
    # ------------------------------------------------------------------
    print("\n[0] Backend health check")
    try:
        r = httpx.get(f"{base}/health", timeout=5)
        check("/health responds 200", r.status_code == 200,
              f"got {r.status_code}: {r.text[:100]}")
    except Exception as e:
        check("/health reachable", False, str(e))
        print("\nBackend is not running. Start it with:")
        print("    uvicorn backend.main:app --reload --port 8000")
        return 1

    # ------------------------------------------------------------------
    # Step 1. Build the SQLite fixture
    # ------------------------------------------------------------------
    print("\n[1] Building SQLite fixture")
    try:
        _create_sqlite(db_path)
        with sqlite3.connect(db_path) as c:
            row_count = c.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        check("SQLite file created", db_path.exists())
        check("Fixture has expected rows", row_count == 300, f"{row_count} rows")
    except Exception as e:
        check("SQLite fixture built", False, str(e))
        return 1

    # ------------------------------------------------------------------
    # Step 2. POST /api/db/connect
    # ------------------------------------------------------------------
    print("\n[2] POST /api/db/connect")
    connect_body = {"type": "sqlite", "database": str(db_path)}
    try:
        r = httpx.post(f"{base}/api/db/connect", json=connect_body, timeout=15)
        check("connect returns 200", r.status_code == 200,
              f"status {r.status_code} body {r.text[:200]}")
        if r.status_code != 200:
            return 1
        body = r.json()
        session_id = body.get("session_id")
        tables = body.get("tables", [])
        check("response has session_id", bool(session_id))
        check("schema lists 'sales' table", "sales" in tables, f"tables: {tables}")
        check("schema lists 'regions' table", "regions" in tables)
    except Exception as e:
        check("connect call succeeded", False, str(e))
        return 1

    # ------------------------------------------------------------------
    # Step 3. POST /api/db/{id}/load — profile the sales table
    # ------------------------------------------------------------------
    print("\n[3] POST /api/db/{id}/load (profile 'sales')")
    try:
        r = httpx.post(f"{base}/api/db/{session_id}/load",
                        json={"table": "sales", "limit": 5000}, timeout=30)
        check("load returns 200", r.status_code == 200,
              f"status {r.status_code} body {r.text[:200]}")
        if r.status_code != 200:
            return 1
        body = r.json()
        profile = body.get("profile", {})
        sheets = profile.get("sheets", {})
        sales_prof = sheets.get("sales", {})
        check("profile has 'sales' sheet", bool(sales_prof))
        check("row_count reported", sales_prof.get("row_count", 0) > 0,
              f"row_count={sales_prof.get('row_count')}")
        cls = sales_prof.get("classification", {})
        check("detected measures include 'amount'",
              "amount" in [m.lower() for m in cls.get("measures", [])],
              f"measures={cls.get('measures')}")
        check("detected date column",
              "order_date" in [d.lower() for d in cls.get("date_columns", [])],
              f"dates={cls.get('date_columns')}")
    except Exception as e:
        check("load call succeeded", False, str(e))
        return 1

    # ------------------------------------------------------------------
    # Step 4. GET /api/dashboard/{id}/overview — auto-dashboard on live DB
    # ------------------------------------------------------------------
    print("\n[4] GET /api/dashboard/{id}/overview (auto-dashboard on live DB)")
    try:
        r = httpx.get(f"{base}/api/dashboard/{session_id}/overview", timeout=30)
        check("overview returns 200", r.status_code == 200,
              f"status {r.status_code} body {r.text[:200]}")
        if r.status_code == 200:
            spec = r.json()
            check("spec has KPIs", len(spec.get("kpis", [])) > 0)
            check("spec has charts", len(spec.get("charts", [])) > 0)
            check("spec has insights", len(spec.get("insights", [])) > 0)
            check("spec has executive_summary",
                  bool(spec.get("executive_summary")))
    except Exception as e:
        check("overview call succeeded", False, str(e))

    # ------------------------------------------------------------------
    # Step 5. POST /api/dashboard/{id}/query (NL query on DB data)
    # ------------------------------------------------------------------
    print("\n[5] POST /api/dashboard/{id}/query — 'top 5 country by amount'")
    try:
        r = httpx.post(f"{base}/api/dashboard/{session_id}/query",
                        json={"question": "top 5 country by amount"}, timeout=30)
        check("query returns 200", r.status_code == 200,
              f"status {r.status_code}")
        if r.status_code == 200:
            spec = r.json()
            intent = spec.get("intent", {})
            check("intent.op is 'top'", intent.get("op") == "top",
                  f"got {intent.get('op')}")
            check("intent.n is 5", intent.get("n") == 5,
                  f"got {intent.get('n')}")
            check("charts include a bar", any(c.get("type") == "bar" for c in spec.get("charts", [])))
    except Exception as e:
        check("NL query on DB session", False, str(e))

    # ------------------------------------------------------------------
    # Step 6. POST /api/db/{id}/query — raw SELECT
    # ------------------------------------------------------------------
    print("\n[6] POST /api/db/{id}/query — raw SELECT")
    try:
        r = httpx.post(f"{base}/api/db/{session_id}/query",
                        json={"sql": "SELECT country, SUM(amount) AS total FROM sales GROUP BY country",
                              "limit": 100}, timeout=15)
        check("raw SELECT returns 200", r.status_code == 200,
              f"status {r.status_code} body {r.text[:200]}")
        if r.status_code == 200:
            body = r.json()
            check("result has 6 country rows", body.get("row_count") == 6,
                  f"got {body.get('row_count')}")
    except Exception as e:
        check("raw SELECT succeeded", False, str(e))

    # ------------------------------------------------------------------
    # Step 7. Security guard — non-SELECT must be rejected
    # ------------------------------------------------------------------
    print("\n[7] Security: non-SELECT is blocked")
    try:
        r = httpx.post(f"{base}/api/db/{session_id}/query",
                        json={"sql": "DELETE FROM sales", "limit": 10}, timeout=10)
        check("DELETE blocked (400)", r.status_code == 400,
              f"status {r.status_code} body {r.text[:120]}")
    except Exception as e:
        check("security guard check", False, str(e))

    # ------------------------------------------------------------------
    # Step 8. DELETE /api/db/{id}
    # ------------------------------------------------------------------
    print("\n[8] DELETE /api/db/{id}")
    try:
        r = httpx.delete(f"{base}/api/db/{session_id}", timeout=10)
        check("disconnect returns 200", r.status_code == 200)
    except Exception as e:
        check("disconnect succeeded", False, str(e))

    # Cleanup
    if not args.keep_db and db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass

    # ------------------------------------------------------------------
    print("\n" + "=" * 55)
    if FAILS:
        print(f"FAILED: {len(FAILS)} check(s) failed")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("PASSED: every DB endpoint works end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
