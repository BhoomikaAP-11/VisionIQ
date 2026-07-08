"""Database connection and query service.

Supports SQL engines via SQLAlchemy (MySQL / PostgreSQL / SQLite / SQL Server)
AND MongoDB via pymongo. The `_connections` dict holds either a SQLAlchemy
Engine or a `_MongoConn` wrapper, keyed by connection_id.
"""
import logging
from sqlalchemy import create_engine, text, inspect

logger = logging.getLogger(__name__)

_connections: dict = {}


class _MongoConn:
    """Wrapper so callers can duck-type against `.kind == 'mongo'`."""
    kind = "mongo"

    def __init__(self, client, database_name: str):
        self.client = client
        self.db = client[database_name]
        self.database_name = database_name

    def dispose(self):
        try:
            self.client.close()
        except Exception:
            pass


def _is_mongo(conn) -> bool:
    return isinstance(conn, _MongoConn)


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
    db_type = str(config.get("type", "")).lower()
    if db_type == "mongodb":
        from pymongo import MongoClient
        uri = config.get("uri")
        if not uri:
            host = config.get("host", "localhost")
            port = config.get("port") or 27017
            user = config.get("user") or ""
            password = config.get("password") or ""
            auth = f"{user}:{password}@" if user else ""
            uri = f"mongodb://{auth}{host}:{port}"
        db_name = config.get("database")
        if not db_name:
            raise ValueError("MongoDB requires a 'database' name")
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        # Ping to verify the connection is actually usable
        client.admin.command("ping")
        _connections[connection_id] = _MongoConn(client, db_name)
        return {"status": "connected", "connection_id": connection_id, "kind": "mongo"}

    # SQL path (SQLAlchemy)
    conn_str = build_connection_string(config)
    engine = create_engine(conn_str, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    _connections[connection_id] = engine
    return {"status": "connected", "connection_id": connection_id, "kind": "sql"}


def disconnect(connection_id: str):
    if connection_id in _connections:
        _connections[connection_id].dispose()
        del _connections[connection_id]


def get_schema(connection_id: str) -> dict:
    conn = _connections.get(connection_id)
    if not conn:
        raise ValueError(f"No connection: {connection_id}")

    # MongoDB path — infer schema by sampling docs
    if _is_mongo(conn):
        schema = {}
        for coll_name in conn.db.list_collection_names():
            fields: dict[str, str] = {}
            row_count = None
            try:
                row_count = conn.db[coll_name].estimated_document_count()
            except Exception:
                pass
            # Sample up to 25 documents to infer field names and types
            try:
                for doc in conn.db[coll_name].find({}, limit=25):
                    for k, v in doc.items():
                        if k in fields:
                            continue
                        fields[k] = type(v).__name__
            except Exception:
                pass
            schema[coll_name] = {
                "columns": [{"name": k, "type": t, "primary_key": k == "_id"}
                             for k, t in fields.items()],
                "foreign_keys": [],
                "row_count": row_count,
            }
        return schema

    # SQL path
    engine = conn
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
    """
    Execute a read query. For SQL connections `sql` must be a SELECT. For
    MongoDB, `sql` is interpreted as `<collection>|<JSON filter>` (filter
    optional) — e.g. `sales` or `sales|{"country":"India"}`.
    """
    conn = _connections.get(connection_id)
    if not conn:
        raise ValueError(f"No connection: {connection_id}")

    if _is_mongo(conn):
        # Mongo: "collection" or "collection|{json filter}"
        import json
        parts = sql.strip().split("|", 1)
        coll_name = parts[0].strip()
        filt = json.loads(parts[1]) if len(parts) == 2 and parts[1].strip() else {}
        cursor = conn.db[coll_name].find(filt).limit(limit)
        rows = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])  # JSON-serialisable
            rows.append(doc)
        columns = list(rows[0].keys()) if rows else []
        return {"columns": columns, "rows": rows, "row_count": len(rows)}

    # SQL path
    engine = conn
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
    """
    Return a pandas DataFrame with up to `limit` rows from `table`.
    For MongoDB, `table` is the collection name.
    """
    import pandas as pd
    import re
    conn = _connections.get(connection_id)
    if not conn:
        raise ValueError(f"No connection: {connection_id}")
    if not re.match(r"^[A-Za-z0-9_\.]+$", table):
        raise ValueError("Invalid table/collection name")

    if _is_mongo(conn):
        docs = list(conn.db[table].find({}, limit=limit))
        for d in docs:
            if "_id" in d:
                d["_id"] = str(d["_id"])  # keep string form for downstream JSON
        return pd.DataFrame(docs)

    engine = conn
    with engine.connect() as sc:
        try:
            return pd.read_sql(text(f"SELECT * FROM {table} LIMIT {limit}"), sc)
        except Exception:
            return pd.read_sql(text(f"SELECT * FROM {table}"), sc).head(limit)


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
