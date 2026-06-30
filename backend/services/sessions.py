"""
In-memory session store for uploaded datasets and DB connections.

Holds the live DataFrame + its profile + conversation history so follow-up
queries (Phase 16) don't need to re-upload. TTL is enforced lazily on access.

For production, swap this for Redis. The public interface (get/put/delete)
stays the same.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional

import pandas as pd

_DEFAULT_TTL_SECONDS = 60 * 60 * 2  # 2 hours


class _Session:
    __slots__ = ("session_id", "kind", "data", "profile", "history",
                 "last_intent", "active_filters", "created", "last_access")

    def __init__(self, kind: str, data: Any, profile: dict):
        self.session_id = uuid.uuid4().hex
        self.kind = kind  # "file" or "db"
        self.data = data  # dict[str, DataFrame] for file, or {connection_id, schema} for db
        self.profile = profile
        self.history: list[dict] = []
        self.last_intent: dict | None = None     # last non-greeting intent
        self.active_filters: list[dict] = []     # accumulating slicers
        now = time.time()
        self.created = now
        self.last_access = now

    def touch(self):
        self.last_access = time.time()


class SessionStore:
    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS):
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        self._ttl = ttl_seconds

    def _sweep(self):
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s.last_access > self._ttl]
        for sid in expired:
            self._sessions.pop(sid, None)

    def create_file_session(self, sheets: dict[str, pd.DataFrame], profile: dict) -> str:
        with self._lock:
            self._sweep()
            session = _Session("file", sheets, profile)
            self._sessions[session.session_id] = session
            return session.session_id

    def create_db_session(self, connection_id: str, schema: dict, profile: dict) -> str:
        with self._lock:
            self._sweep()
            session = _Session("db", {"connection_id": connection_id, "schema": schema}, profile)
            self._sessions[session.session_id] = session
            return session.session_id

    def get(self, session_id: str) -> Optional[_Session]:
        with self._lock:
            self._sweep()
            session = self._sessions.get(session_id)
            if session:
                session.touch()
            return session

    def get_dataframe(self, session_id: str, sheet: Optional[str] = None) -> Optional[pd.DataFrame]:
        session = self.get(session_id)
        if not session:
            return None
        if session.kind == "file":
            sheets: dict[str, pd.DataFrame] = session.data
            if sheet and sheet in sheets:
                return sheets[sheet]
            primary = session.profile.get("primary_sheet")
            if primary and primary in sheets:
                return sheets[primary]
            return next(iter(sheets.values())) if sheets else None
        if session.kind == "db":
            # Set by POST /api/db/{id}/load
            return session.data.get("dataframe")
        return None

    def append_history(self, session_id: str, entry: dict):
        session = self.get(session_id)
        if session:
            session.history.append({"ts": time.time(), **entry})
            # Cap history length
            session.history = session.history[-50:]

    def delete(self, session_id: str):
        with self._lock:
            self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[dict]:
        with self._lock:
            self._sweep()
            return [
                {
                    "session_id": s.session_id,
                    "kind": s.kind,
                    "created": s.created,
                    "last_access": s.last_access,
                    "history_length": len(s.history),
                }
                for s in self._sessions.values()
            ]


# Module-level singleton — import from here
store = SessionStore()
