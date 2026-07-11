"""
Query log — append every query + classifier prediction to a JSONL file
so real user queries become training data over time.

Every entry is a single line of JSON:
    {"ts": "2026-07-08T12:34:56Z", "question": "...",
     "predicted_class": "top_bottom", "predicted_confidence": 0.87,
     "final_op": "top", "final_confidence": 0.92, "source": "heuristic"}

To promote real queries into the SEED_DATA:
    python -m backend.services.query_log --review
prints the last N queries with predicted labels; hand-edit and paste the good
ones into query_classifier.SEED_DATA.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PATH = Path(os.getenv("QUERY_LOG_PATH", "backend/uploads/query_log.jsonl"))
_lock = threading.Lock()


def log(question: str, classification: dict | None, intent: dict | None):
    """Append one query event. Best-effort — never raises."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "question": question,
            "predicted_class": (classification or {}).get("label"),
            "predicted_confidence": (classification or {}).get("confidence"),
            "final_op": (intent or {}).get("op"),
            "final_confidence": (intent or {}).get("confidence"),
            "source": (intent or {}).get("source", "heuristic"),
        }
        with _lock:
            with _LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("query_log write failed: %s", e)


def review(n: int = 50) -> list[dict]:
    """Return the last `n` log entries for manual review."""
    if not _LOG_PATH.exists():
        return []
    lines = _LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines[-n:]]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=int, default=50,
                         help="Print the last N log entries")
    args = parser.parse_args()
    for entry in review(args.review):
        print(json.dumps(entry, indent=None))
