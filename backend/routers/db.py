"""
Database connection endpoints.

POST /api/db/connect            -> open connection
POST /api/db/{session_id}/load  -> profile a table and turn the DB session
                                   into a queryable file-style session
POST /api/db/{session_id}/query -> raw SELECT against the live connection
DELETE /api/db/{session_id}     -> close
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from ..services import db_service, profiling
from ..services.sessions import store

router = APIRouter(prefix="/api/db", tags=["database"])


class ConnectBody(BaseModel):
    type: str = Field(..., description="mysql | postgresql | sqlite | sqlserver")
    host: str | None = "localhost"
    port: int | None = None
    user: str | None = ""
    password: SecretStr | None = None
    database: str = Field(..., min_length=1)


class LoadBody(BaseModel):
    table: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=5000, ge=10, le=200_000)


class QueryBody(BaseModel):
    sql: str = Field(..., min_length=4, max_length=10_000)
    limit: int = Field(default=1000, ge=1, le=10_000)


@router.post("/connect")
def connect(body: ConnectBody):
    config = body.model_dump()
    if body.password is not None:
        config["password"] = body.password.get_secret_value()
    connection_id = uuid.uuid4().hex[:8]
    try:
        db_service.connect(connection_id, config)
        schema = db_service.get_schema(connection_id)
    except Exception as e:
        raise HTTPException(400, f"Connection failed: {e}")

    # Minimal stub profile until the user picks a table
    profile = {
        "sheet_count": len(schema),
        "total_rows": 0,
        "primary_sheet": next(iter(schema)) if schema else None,
        "sheets": {},  # populated by /load
    }
    session_id = store.create_db_session(connection_id, schema, profile)
    return {
        "session_id": session_id,
        "connection_id": connection_id,
        "schema": schema,
        "tables": list(schema.keys()),
    }


@router.post("/{session_id}/load")
def load_table(session_id: str, body: LoadBody):
    """
    Pull a sample from the chosen table, profile it, and stash the DataFrame
    in the session so dashboard endpoints can use the same code path as files.
    """
    session = store.get(session_id)
    if not session or session.kind != "db":
        raise HTTPException(404, "DB session not found")
    connection_id = session.data["connection_id"]
    try:
        df = db_service.fetch_table_sample(connection_id, body.table, limit=body.limit)
    except Exception as e:
        raise HTTPException(400, f"Failed to load table: {e}")

    sheet_profile, aug_df = profiling.profile_dataframe(df, name=body.table)
    # Re-key the session as a hybrid: keep DB connection AND the loaded df
    session.data["dataframe"] = aug_df
    session.data["loaded_table"] = body.table
    session.profile = {
        "sheet_count": 1,
        "total_rows": sheet_profile["row_count"],
        "primary_sheet": body.table,
        "sheets": {body.table: sheet_profile},
    }
    return {"status": "loaded", "table": body.table, "profile": session.profile}


@router.post("/{session_id}/query")
def query(session_id: str, body: QueryBody):
    session = store.get(session_id)
    if not session or session.kind != "db":
        raise HTTPException(404, "Database session not found")
    connection_id = session.data["connection_id"]
    try:
        return db_service.run_query(connection_id, body.sql, body.limit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")


@router.delete("/{session_id}")
def disconnect(session_id: str):
    session = store.get(session_id)
    if session and session.kind == "db":
        db_service.disconnect(session.data["connection_id"])
    store.delete(session_id)
    return {"status": "disconnected"}
