"""Tiny SQLite event log."""
import sqlite3
import threading
import time

from . import config

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _lock, _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS events (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts REAL NOT NULL,
                   level INTEGER NOT NULL,
                   message TEXT NOT NULL,
                   snapshot TEXT,
                   emailed INTEGER NOT NULL DEFAULT 0
               )"""
        )


def add_event(level: int, message: str, snapshot: str | None = None, emailed: bool = False) -> int:
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO events (ts, level, message, snapshot, emailed) VALUES (?,?,?,?,?)",
            (time.time(), level, message, snapshot, int(emailed)),
        )
        return cur.lastrowid


def mark_emailed(event_id: int, ok: bool) -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE events SET emailed=? WHERE id=?", (1 if ok else -1, event_id))


def recent(limit: int = 50, min_level: int = 1) -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT * FROM events WHERE level>=? ORDER BY id DESC LIMIT ?",
            (min_level, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def counts() -> dict:
    with _lock, _conn() as c:
        rows = c.execute("SELECT level, COUNT(*) AS n FROM events GROUP BY level").fetchall()
    return {r["level"]: r["n"] for r in rows}
