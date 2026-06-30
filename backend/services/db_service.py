"""Database connection and query service."""
import logging
from sqlalchemy import create_engine, text, inspect

logger = logging.getLogger(__name__)

_connections: dict = {}


def build_connection_string(config: dict) -> str:
    db_type = config.get("type", "").lower()
    host = config.get("host", "localhost")
    port = config.get("port", "")
    user = config.get("user", "")
    password = config.get("password", "")
    database = config.get("database", "")

    if db_type == "mysql":
        port = port or 3306
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
    elif db_type == "postgresql":
        port = port or 5432
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
    elif db_type == "sqlite":
        return f"sqlite:///{database}"
    elif db_type == "sqlserver":
        port = port or 1433
        return f"mssql+pyodbc://{user}:{password}@{host}:{port}/{database}?driver=ODBC+Driver+17+for+SQL+Server"
    else:
        raise ValueError(f"Unsupported database type: {db_type}")


def connect(connection_id: str, config: dict) -> dict:
    conn_str = build_connection_string(config)
    engine = create_engine(conn_str, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    _connections[connection_id] = engine
    return {"status": "connected", "connection_id": connection_id}


def disconnect(connection_id: str):
    if connection_id in _connections:
        _connections[connection_id].dispose()
        del _connections[connection_id]


def get_schema(connection_id: str) -> dict:
    engine = _connections.get(connection_id)
    if not engine:
        raise ValueError(f"No connection: {connection_id}")
    inspector = inspect(engine)
    schema = {}
    for table_name in inspector.get_table_names():
        cols = inspector.get_columns(table_name)
        pks = inspector.get_pk_constraint(table_name).get("constrained_columns", [])
        fks = inspector.get_foreign_keys(table_name)
        schema[table_name] = {
            "columns": [{"name": c["name"], "type": str(c["type"]), "primary_key": c["name"] in pks} for c in cols],
            "foreign_keys": [{"column": fk["constrained_columns"], "references": f"{fk['referred_table']}.{fk['referred_columns']}"} for fk in fks],
            "row_count": None,
        }
    return schema


def run_query(connection_id: str, sql: str, limit: int = 1000) -> dict:
    engine = _connections.get(connection_id)
    if not engine:
        raise ValueError(f"No connection: {connection_id}")
    safe_sql = sql.strip().rstrip(";")
    if not safe_sql.upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed")
    # Some dialects (MSSQL) don't accept LIMIT — handle gracefully
    wrapped = safe_sql
    if "LIMIT" not in safe_sql.upper() and "TOP " not in safe_sql.upper()[:50]:
        wrapped = f"{safe_sql} LIMIT {limit}"
    with engine.connect() as conn:
        try:
            result = conn.execute(text(wrapped))
        except Exception:
            # Fallback: run without LIMIT and slice in Python
            result = conn.execute(text(safe_sql))
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchmany(limit)]
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def fetch_table_sample(connection_id: str, table: str, limit: int = 5000):
    """Return a pandas DataFrame with up to `limit` rows from `table`."""
    import pandas as pd
    engine = _connections.get(connection_id)
    if not engine:
        raise ValueError(f"No connection: {connection_id}")
    # Sanitise — only allow alphanumerics, underscore, dot (schema.table)
    import re
    if not re.match(r"^[A-Za-z0-9_\.]+$", table):
        raise ValueError("Invalid table name")
    with engine.connect() as conn:
        try:
            return pd.read_sql(text(f"SELECT * FROM {table} LIMIT {limit}"), conn)
        except Exception:
            return pd.read_sql(text(f"SELECT * FROM {table}"), conn).head(limit)


def build_schema_context(schema: dict) -> str:
    lines = []
    for table, info in schema.items():
        lines.append(f"Table: {table}")
        for col in info["columns"]:
            pk = " [PK]" if col["primary_key"] else ""
            lines.append(f"  - {col['name']} ({col['type']}){pk}")
        for fk in info.get("foreign_keys", []):
            lines.append(f"  FK: {fk['column']} -> {fk['references']}")
        lines.append("")
    return "\n".join(lines)
