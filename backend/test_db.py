import sqlite3
import pandas as pd
from sqlalchemy import create_engine, text

def main():
    conn = sqlite3.connect('test.db')
    conn.execute("CREATE TABLE IF NOT EXISTS sales (id INTEGER PRIMARY KEY, item TEXT, amount REAL)")
    conn.execute("INSERT INTO sales (item, amount) VALUES ('apple', 10.5)")
    conn.commit()
    conn.close()

    engine = create_engine('sqlite:///test.db')
    with engine.connect() as sc:
        # test read_sql
        try:
            df = pd.read_sql(text("SELECT * FROM sales LIMIT 5000"), sc)
            print("read_sql LIMIT success, df size:", len(df))
        except Exception as e:
            print("read_sql LIMIT error:", e)

    with engine.connect() as sc:
        try:
            result = sc.execute(text("SELECT * FROM sales LIMIT 10"))
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            print("run_query rows:", rows)
        except Exception as e:
            print("run_query error:", e)

if __name__ == '__main__':
    main()
