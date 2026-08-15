"""Durable SQLite storage for PathPilot conversation/event transcripts.

Every push_event() call in app.py (narration, transcript, answer,
verification, safety, state) is mirrored here in addition to the existing
per-session .events.jsonl file. SQLite gives PathPilot a queryable, durable
record that survives extension reloads, side-panel crashes, and controller
restarts -- so "nothing is saved" failures are no longer possible even if
the in-memory JS state is lost.

No page content, cookies, credentials, or screenshots are ever stored here
-- only narration/transcript/answer/verification/safety event text that is
already being sent to the browser UI.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(r"D:\hermes\data\runtime\pathpilot_transcripts.sqlite3")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS transcript_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                event_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                step_id TEXT,
                text TEXT NOT NULL,
                meta_json TEXT,
                created_at TEXT NOT NULL
            )
        """)
        _conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_transcript_session
            ON transcript_events (session_id, event_id)
        """)
        _conn.commit()
    return _conn


def record_event(session_id: str, event_id: int, event_type: str, text: str,
                  step_id: str | None = None, meta_json: str | None = None) -> None:
    """Append one event to the durable SQLite transcript log. Never raises --
    a transcript-storage failure must not break the live walkthrough."""
    try:
        with _lock:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO transcript_events "
                "(session_id, event_id, event_type, step_id, text, meta_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, event_id, event_type, step_id, text, meta_json,
                 time.strftime("%Y-%m-%dT%H:%M:%S%z")),
            )
            conn.commit()
    except Exception:
        pass


def load_transcript(session_id: str) -> list[dict[str, Any]]:
    """Return the full durable transcript for one session, oldest first."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT event_id, event_type, step_id, text, meta_json, created_at "
            "FROM transcript_events WHERE session_id = ? ORDER BY event_id ASC",
            (session_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "event_type": r[1], "step_id": r[2], "text": r[3],
            "meta_json": r[4], "created_at": r[5],
        }
        for r in rows
    ]


def list_sessions() -> list[str]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT DISTINCT session_id FROM transcript_events").fetchall()
    return [r[0] for r in rows]
