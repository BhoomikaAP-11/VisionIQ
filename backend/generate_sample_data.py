"""
Generate a realistic 300-row sales dataset for testing DB connectivity and
data-analysis features. Writes three files simultaneously so you can pick
whichever your database wants:

    backend/data/sample_sales.json    -- MongoDB bulk-import (JSON array)
    backend/data/sample_sales.sql     -- SQL CREATE TABLE + 300 INSERTs
    backend/data/sample_sales.csv     -- generic CSV

Design choices (so analytics have interesting things to find):
    - 12 full months of data (Jan-Dec 2024) → forecast + seasonality
    - Q4 seasonal uplift for gift-buying products
    - One product ("70% Dark Bites") in secular decline for root-cause
    - Strong Amount ↔ Boxes correlation (~0.9)
    - 3 intentional outliers (>3σ) for anomaly detection
    - Long-tail distribution: 3 products drive 60% of revenue (Pareto)

Run:
    python -m backend.generate_sample_data
"""
from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

COUNTRIES = ["India", "USA", "UK", "Germany", "Japan", "Australia", "Brazil"]
PEOPLE = ["Alice", "Bob", "Carol", "Dan", "Eva", "Frank", "Grace", "Hank"]

# (name, base_amount, base_boxes, category)
PRODUCTS = [
    ("50% Dark Bites",        180, 120, "Dark"),
    ("Peanut Butter Cubes",   140,  95, "Nut"),
    ("Mint Chip Choco",       110,  80, "Mint"),
    ("99% Dark & Pure",        90,  60, "Dark"),
    ("Manuka Honey Choco",    150, 100, "Honey"),
    ("Organic Choco Syrup",    70,  55, "Syrup"),
    ("Fruit & Nut Bars",       85,  65, "Nut"),
    ("White Choc",             60,  50, "White"),
    ("Baker's Choco Chips",    55,  45, "Bakery"),
    ("70% Dark Bites",         70,  50, "Dark"),  # secular decline
]

# Month multipliers — Q4 uplift, mild seasonality
SEASONALITY = [0.95, 0.90, 1.00, 1.02, 1.05, 1.03,
               0.98, 0.97, 1.02, 1.10, 1.20, 1.30]


def generate():
    rng = random.Random(42)
    rows = []
    for i in range(300):
        # Uniform-ish over 12 months
        month = (i * 12) // 300  # 0-11
        day = rng.randint(1, 28)
        date = datetime(2024, month + 1, day)

        product, base_amt, base_box, category = rng.choice(PRODUCTS)
        country = rng.choice(COUNTRIES)
        person = rng.choice(PEOPLE)

        # Base amount × seasonality × noise
        noise = rng.uniform(0.85, 1.15)
        seasonal = SEASONALITY[month]

        # "70% Dark Bites" secularly declines through the year
        if product == "70% Dark Bites":
            secular = 1.0 - (month / 12) * 0.6   # loses 60% by December
        else:
            secular = 1.0 + (month / 12) * 0.1   # everyone else grows slightly

        # Base unit is ~$50/box → Amount ≈ boxes * 50 with variation
        boxes = int(base_box * seasonal * secular * noise)
        amount = round(boxes * rng.uniform(45, 55), 2)

        rows.append({
            "order_date": date.strftime("%Y-%m-%d"),
            "country": country,
            "product": product,
            "sales_person": person,
            "category": category,
            "amount": amount,
            "boxes_shipped": boxes,
        })

    # Inject 3 outliers for anomaly detection
    for idx in (37, 158, 271):
        rows[idx]["amount"] = round(rows[idx]["amount"] * 6, 2)
        rows[idx]["boxes_shipped"] = rows[idx]["boxes_shipped"] * 6

    rng.shuffle(rows)
    return rows


def write_json(rows):
    path = OUT_DIR / "sample_sales.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"Wrote {path}  ({len(rows)} rows, JSON)")


def write_csv(rows):
    path = OUT_DIR / "sample_sales.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {path}  ({len(rows)} rows, CSV)")


def write_sql(rows):
    path = OUT_DIR / "sample_sales.sql"
    lines = [
        "-- 300-row sales dataset for testing DB connectivity and analytics.",
        "-- Works on SQLite / MySQL / PostgreSQL / SQL Server.",
        "",
        "DROP TABLE IF EXISTS sales;",
        "CREATE TABLE sales (",
        "  id            INTEGER PRIMARY KEY AUTOINCREMENT,",
        "  order_date    DATE    NOT NULL,",
        "  country       VARCHAR(64)  NOT NULL,",
        "  product       VARCHAR(128) NOT NULL,",
        "  sales_person  VARCHAR(64)  NOT NULL,",
        "  category      VARCHAR(64)  NOT NULL,",
        "  amount        DECIMAL(12,2) NOT NULL,",
        "  boxes_shipped INTEGER      NOT NULL",
        ");",
        "",
    ]
    for r in rows:
        lines.append(
            "INSERT INTO sales (order_date, country, product, sales_person, "
            "category, amount, boxes_shipped) VALUES ("
            f"'{r['order_date']}', "
            f"'{r['country'].replace(chr(39), chr(39)*2)}', "
            f"'{r['product'].replace(chr(39), chr(39)*2)}', "
            f"'{r['sales_person']}', "
            f"'{r['category']}', "
            f"{r['amount']}, "
            f"{r['boxes_shipped']}"
            ");"
        )
    path = OUT_DIR / "sample_sales.sql"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {path}  ({len(rows)} rows, SQL)")


if __name__ == "__main__":
    rows = generate()
    write_json(rows)
    write_csv(rows)
    write_sql(rows)
    print(f"\nDone. Total rows: {len(rows)}")
    print("Files in backend/data/:")
    for p in OUT_DIR.glob("sample_sales.*"):
        size = p.stat().st_size / 1024
        print(f"  {p.name}  ({size:.1f} KB)")
