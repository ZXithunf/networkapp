"""SQLite storage for uptime check history (standard library sqlite3)."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL,
    status_code INTEGER,
    response_ms REAL    NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT,
    checked_at  TEXT    NOT NULL
)
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    # One short-lived connection per call: simple and safe across threads and
    # the two uvicorn worker processes. timeout waits on a locked database.
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(data_dir: str | Path) -> Path:
    """Create the data directory and table if needed; return the db path."""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    db_path = directory / "netpulse.db"
    with _connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        conn.execute(SCHEMA)
    return db_path


def save_check(db_path: Path, result: dict) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO checks (url, status_code, response_ms, ok, error, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",  # parameterised: no SQL injection
            (
                result["url"],
                result["status_code"],
                result["response_ms"],
                int(result["ok"]),
                result.get("error"),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
    conn.close()  # the context manager commits but does not close


def recent_checks(db_path: Path, limit: int = 20) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT url, status_code, response_ms, ok, error, checked_at "
            "FROM checks ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [{**dict(row), "ok": bool(row["ok"])} for row in rows]
