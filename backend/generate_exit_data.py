"""
Generate a realistic 250-row Exit Interview dataset for testing HR / survey
analytics. Writes an Excel .xlsx and a .csv so you can pick either upload
path.

Columns:
    EmployeeID          — unique ID (dimension, high cardinality)
    Gender              — Male / Female / Non-binary / Prefer not to say
    AgeGroup            — 20-29, 30-39, 40-49, 50+
    Department          — Engineering / Sales / Marketing / HR / Finance /
                          Operations / Product / Customer Support
    Position            — Analyst / Senior Analyst / Manager / Director /
                          VP / Individual Contributor
    Location            — Bengaluru / Mumbai / Delhi / Chennai / Pune /
                          Hyderabad / Remote
    JoinDate            — random within past 8 years
    ExitDate            — after JoinDate; controls tenure
    TenureMonths        — numeric measure (derived)
    ManagerRating       — 1-5 (numeric measure)
    SatisfactionScore   — 1-10 (numeric measure)
    ReasonForLeaving    — Better Opportunity / Compensation / Work-Life
                          Balance / Manager Issues / Career Growth /
                          Relocation / Health / Personal / Retirement
    WouldRecommend      — Yes / No / Maybe
    HasCounteroffer     — Yes / No

Design choices for interesting analytics:
    - Engineering + Sales are attrition-heavy (test root_cause)
    - "Compensation" and "Career Growth" dominate exit reasons (test Pareto)
    - Tenure is right-skewed with a spike at 12-24 months (test histogram)
    - Gender split roughly 60/40 male/female (test count queries)
    - Manager Rating correlates negatively with attrition (test correlation)
    - Salary Band as an ordinal encoding of the position level

Run:
    python -m backend.generate_exit_data

Files land in:
    backend/data/exit_interviews.xlsx
    backend/data/exit_interviews.csv
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

GENDERS = [("Male", 55), ("Female", 38), ("Non-binary", 4), ("Prefer not to say", 3)]
AGE_GROUPS = [("20-29", 42), ("30-39", 38), ("40-49", 15), ("50+", 5)]
DEPARTMENTS = [
    ("Engineering", 30), ("Sales", 22), ("Marketing", 12), ("Customer Support", 10),
    ("Operations", 10), ("Product", 8), ("Finance", 5), ("HR", 3),
]
POSITIONS = [
    ("Analyst", 25), ("Senior Analyst", 22), ("Individual Contributor", 20),
    ("Manager", 18), ("Senior Manager", 8), ("Director", 5), ("VP", 2),
]
LOCATIONS = [
    ("Bengaluru", 32), ("Mumbai", 18), ("Delhi", 15), ("Pune", 12),
    ("Chennai", 10), ("Hyderabad", 8), ("Remote", 5),
]
# Weight compensation + career growth higher to give Pareto a clean 80/20
REASONS = [
    ("Compensation", 24), ("Career Growth", 22), ("Better Opportunity", 18),
    ("Work-Life Balance", 12), ("Manager Issues", 9), ("Relocation", 6),
    ("Health", 4), ("Personal", 3), ("Retirement", 2),
]
RECOMMEND = [("Yes", 55), ("No", 30), ("Maybe", 15)]
COUNTEROFFER = [("No", 78), ("Yes", 22)]


def weighted(choices, rng):
    total = sum(w for _, w in choices)
    r = rng.uniform(0, total)
    upto = 0
    for name, w in choices:
        upto += w
        if r <= upto:
            return name
    return choices[-1][0]


def generate():
    rng = random.Random(42)
    rows = []
    today = datetime(2025, 6, 30)

    for i in range(1, 251):
        gender = weighted(GENDERS, rng)
        age = weighted(AGE_GROUPS, rng)
        dept = weighted(DEPARTMENTS, rng)
        # Tie position to age loosely
        if age == "50+":
            pos = weighted([("Director", 30), ("VP", 20), ("Senior Manager", 30), ("Manager", 20)], rng)
        elif age == "40-49":
            pos = weighted([("Manager", 30), ("Senior Manager", 25), ("Director", 20), ("Senior Analyst", 25)], rng)
        elif age == "20-29":
            pos = weighted([("Analyst", 60), ("Individual Contributor", 30), ("Senior Analyst", 10)], rng)
        else:
            pos = weighted(POSITIONS, rng)
        loc = weighted(LOCATIONS, rng)
        reason = weighted(REASONS, rng)

        # Tenure: right-skewed, 3-96 months, spike at 12-24
        r = rng.random()
        if r < 0.35:
            tenure = rng.randint(12, 24)
        elif r < 0.65:
            tenure = rng.randint(6, 36)
        elif r < 0.9:
            tenure = rng.randint(24, 60)
        else:
            tenure = rng.randint(60, 96)

        exit_date = today - timedelta(days=rng.randint(0, 730))
        join_date = exit_date - timedelta(days=int(tenure * 30.4))

        # Manager rating: skewed lower for high-attrition departments
        base_rating = 3.5 if dept in ("Engineering", "Sales") else 3.9
        rating = max(1, min(5, round(rng.gauss(base_rating, 0.9))))

        # Satisfaction correlates with rating
        sat = max(1, min(10, round(rating * 2 + rng.gauss(0, 1.5))))

        recommend = weighted(RECOMMEND, rng) if sat >= 5 else "No"
        counteroffer = weighted(COUNTEROFFER, rng)

        rows.append({
            "EmployeeID": f"E{2000 + i:05d}",
            "Gender": gender,
            "AgeGroup": age,
            "Department": dept,
            "Position": pos,
            "Location": loc,
            "JoinDate": join_date.strftime("%Y-%m-%d"),
            "ExitDate": exit_date.strftime("%Y-%m-%d"),
            "TenureMonths": tenure,
            "ManagerRating": rating,
            "SatisfactionScore": sat,
            "ReasonForLeaving": reason,
            "WouldRecommend": recommend,
            "HasCounteroffer": counteroffer,
        })

    rng.shuffle(rows)
    return rows


def write_csv(rows):
    path = OUT_DIR / "exit_interviews.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {path}  ({len(rows)} rows)")


def write_xlsx(rows):
    path = OUT_DIR / "exit_interviews.xlsx"
    try:
        from openpyxl import Workbook
    except ImportError:
        print("openpyxl not installed — skipping .xlsx. Install: pip install openpyxl")
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "ExitInterviews"
    headers = list(rows[0].keys())
    ws.append(headers)
    for r in rows:
        ws.append([r[h] for h in headers])
    # Freeze header row + auto-filter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    print(f"Wrote {path}  ({len(rows)} rows)")


def stats(rows):
    from collections import Counter
    print("\nDataset composition:")
    for col in ("Department", "Gender", "ReasonForLeaving"):
        c = Counter(r[col] for r in rows)
        top = c.most_common(3)
        print(f"  {col}: {', '.join(f'{k}={v}' for k, v in top)}")
    tenures = [r["TenureMonths"] for r in rows]
    print(f"  TenureMonths: min={min(tenures)} max={max(tenures)} "
          f"avg={sum(tenures)/len(tenures):.1f}")


if __name__ == "__main__":
    rows = generate()
    write_csv(rows)
    write_xlsx(rows)
    stats(rows)
    print(f"\nDone. Files in {OUT_DIR}")
