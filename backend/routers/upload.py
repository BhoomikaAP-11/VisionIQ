"""
File upload + dataset session creation.

POST /api/upload                  -> create session from .xlsx/.xls/.csv
GET  /api/sessions                -> list active sessions
GET  /api/sessions/{id}/profile   -> full workbook profile
DELETE /api/sessions/{id}         -> drop a session
"""
from __future__ import annotations

import os
import shutil
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..services import excel_service
from ..services.sessions import store

router = APIRouter(prefix="/api", tags=["upload"])

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "500"))
ALLOWED_EXT = {".xlsx", ".xls", ".xlsm", ".csv"}

os.makedirs(UPLOAD_DIR, exist_ok=True)


def _secure_filename(name: str) -> str:
    # Strip path components; keep extension; replace anything weird with _
    base = os.path.basename(name or "upload")
    base = "".join(c if c.isalnum() or c in (".", "-", "_") else "_" for c in base)
    return base[:200] or "upload"


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type {ext}. Allowed: {sorted(ALLOWED_EXT)}")

    safe_name = _secure_filename(file.filename)
    file_id = uuid.uuid4().hex[:8]
    save_path = os.path.join(UPLOAD_DIR, f"{file_id}_{safe_name}")

    # Stream to disk, enforcing size cap
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with open(save_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    os.remove(save_path)
                    raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB}MB limit")
                out.write(chunk)

        data = excel_service.read_file(save_path)
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(400, f"Failed to read file: {e}")

    session_id = store.create_file_session(data["sheets"], data["profile"])

    return {
        "session_id": session_id,
        "filename": safe_name,
        "size_bytes": written,
        "profile": data["profile"],
    }


@router.get("/sessions")
def list_sessions():
    return {"sessions": store.list_sessions()}


@router.get("/sessions/{session_id}/profile")
def get_profile(session_id: str):
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")
    return session.profile


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    store.delete(session_id)
    return {"status": "deleted", "session_id": session_id}
