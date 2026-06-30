"""
AI-powered insights & SQL generation.

POST /api/insights/{session_id}    -> structured executive insights
POST /api/sql/{session_id}         -> natural-language to SQL (DB sessions)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import ai_service, excel_service
from ..services.sessions import store

router = APIRouter(prefix="/api", tags=["ai"])


class InsightBody(BaseModel):
    question: str | None = Field(default=None, max_length=500)
    sheet: str | None = None


class SqlBody(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)


@router.post("/insights/{session_id}")
async def insights(session_id: str, body: InsightBody):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")

    schema_context = excel_service.build_schema_context({"profile": session.profile})
    df = store.get_dataframe(session_id, body.sheet) if session.kind == "file" else None
    data_summary = schema_context
    if df is not None and len(df):
        head_csv = df.head(50).to_csv(index=False)
        data_summary = f"{schema_context}\n\nSample rows (first 50):\n{head_csv}"

    try:
        result = await ai_service.generate_insights(data_summary, body.question or "")
    except Exception as e:
        raise HTTPException(503, f"AI providers unavailable: {e}")

    store.append_history(session_id, {"type": "insight", "question": body.question})
    return result


@router.post("/sql/{session_id}")
async def generate_sql(session_id: str, body: SqlBody):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")
    schema_context = excel_service.build_schema_context({"profile": session.profile})
    try:
        result = await ai_service.generate_sql(body.question, schema_context)
    except Exception as e:
        raise HTTPException(503, f"AI providers unavailable: {e}")
    store.append_history(session_id, {"type": "sql", "question": body.question})
    return result
