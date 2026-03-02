"""SQLite database layer for Claude Code Scheduler."""

import sqlite3
import os
import json
from datetime import datetime
from typing import Optional


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduler.db")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection):
    """Create all tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schedules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            working_dir TEXT NOT NULL DEFAULT '.',
            frequency   TEXT NOT NULL DEFAULT 'once'
                        CHECK(frequency IN ('once','hourly','daily','weekly')),
            scheduled_time TEXT NOT NULL DEFAULT '09:00',
            day_of_week INTEGER DEFAULT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS schedule_prompts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id   INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
            prompt_order  INTEGER NOT NULL,
            prompt_text   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id   INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
            started_at    TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at   TEXT,
            status        TEXT NOT NULL DEFAULT 'running'
                          CHECK(status IN ('running','success','partial_failure','failure','error')),
            session_id    TEXT,
            total_cost_usd REAL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS prompt_results (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            prompt_id     INTEGER,
            prompt_order  INTEGER NOT NULL,
            prompt_text   TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending'
                          CHECK(status IN ('pending','running','success','failure','skipped')),
            result_text   TEXT,
            result_json   TEXT,
            error_message TEXT,
            cost_usd      REAL DEFAULT 0.0,
            duration_ms   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # Seed default settings
    defaults = {
        "check_interval_seconds": "30",
        "run_on_startup": "true",
        "minimize_to_tray": "true",
        "notify_on_success": "true",
        "notify_on_failure": "true",
        "claude_executable": "claude",
        "dangerously_skip_permissions": "true",
        "history_retention_days": "90",
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v)
        )
    conn.commit()


# ── Settings ──────────────────────────────────────────────────────────────

def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()


# ── Schedules ─────────────────────────────────────────────────────────────

def create_schedule(
    conn: sqlite3.Connection,
    name: str,
    working_dir: str,
    frequency: str,
    scheduled_time: str,
    day_of_week: Optional[int],
    prompts: list[str],
) -> int:
    cur = conn.execute(
        """INSERT INTO schedules(name, working_dir, frequency, scheduled_time, day_of_week)
           VALUES (?, ?, ?, ?, ?)""",
        (name, working_dir, frequency, scheduled_time, day_of_week),
    )
    schedule_id = cur.lastrowid
    for i, prompt in enumerate(prompts):
        conn.execute(
            "INSERT INTO schedule_prompts(schedule_id, prompt_order, prompt_text) VALUES (?, ?, ?)",
            (schedule_id, i, prompt),
        )
    conn.commit()
    return schedule_id


def update_schedule(
    conn: sqlite3.Connection,
    schedule_id: int,
    name: str,
    working_dir: str,
    frequency: str,
    scheduled_time: str,
    day_of_week: Optional[int],
    prompts: list[str],
):
    conn.execute(
        """UPDATE schedules SET name=?, working_dir=?, frequency=?, scheduled_time=?,
           day_of_week=?, updated_at=datetime('now') WHERE id=?""",
        (name, working_dir, frequency, scheduled_time, day_of_week, schedule_id),
    )
    conn.execute("DELETE FROM schedule_prompts WHERE schedule_id=?", (schedule_id,))
    for i, prompt in enumerate(prompts):
        conn.execute(
            "INSERT INTO schedule_prompts(schedule_id, prompt_order, prompt_text) VALUES (?, ?, ?)",
            (schedule_id, i, prompt),
        )
    conn.commit()


def delete_schedule(conn: sqlite3.Connection, schedule_id: int):
    conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    conn.commit()


def toggle_schedule(conn: sqlite3.Connection, schedule_id: int, enabled: bool):
    conn.execute(
        "UPDATE schedules SET enabled=?, updated_at=datetime('now') WHERE id=?",
        (1 if enabled else 0, schedule_id),
    )
    conn.commit()


def get_all_schedules(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM schedules ORDER BY id").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        prompts = conn.execute(
            "SELECT * FROM schedule_prompts WHERE schedule_id=? ORDER BY prompt_order",
            (d["id"],),
        ).fetchall()
        d["prompts"] = [dict(p) for p in prompts]
        # Attach latest run status
        latest = conn.execute(
            "SELECT status FROM runs WHERE schedule_id=? ORDER BY id DESC LIMIT 1",
            (d["id"],),
        ).fetchone()
        d["last_status"] = latest["status"] if latest else None
        result.append(d)
    return result


def get_schedule(conn: sqlite3.Connection, schedule_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    prompts = conn.execute(
        "SELECT * FROM schedule_prompts WHERE schedule_id=? ORDER BY prompt_order",
        (d["id"],),
    ).fetchall()
    d["prompts"] = [dict(p) for p in prompts]
    return d


# ── Runs ──────────────────────────────────────────────────────────────────

def create_run(conn: sqlite3.Connection, schedule_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO runs(schedule_id) VALUES (?)", (schedule_id,)
    )
    conn.commit()
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    session_id: Optional[str] = None,
    total_cost_usd: float = 0.0,
):
    conn.execute(
        """UPDATE runs SET finished_at=datetime('now'), status=?, session_id=?,
           total_cost_usd=? WHERE id=?""",
        (status, session_id, total_cost_usd, run_id),
    )
    conn.commit()


def get_runs(
    conn: sqlite3.Connection,
    schedule_id: Optional[int] = None,
    limit: int = 100,
) -> list[dict]:
    if schedule_id:
        rows = conn.execute(
            """SELECT r.*, s.name as schedule_name FROM runs r
               JOIN schedules s ON r.schedule_id=s.id
               WHERE r.schedule_id=? ORDER BY r.id DESC LIMIT ?""",
            (schedule_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT r.*, s.name as schedule_name FROM runs r
               JOIN schedules s ON r.schedule_id=s.id
               ORDER BY r.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(conn: sqlite3.Connection, run_id: int) -> Optional[dict]:
    row = conn.execute(
        """SELECT r.*, s.name as schedule_name FROM runs r
           JOIN schedules s ON r.schedule_id=s.id WHERE r.id=?""",
        (run_id,),
    ).fetchone()
    return dict(row) if row else None


# ── Prompt Results ────────────────────────────────────────────────────────

def create_prompt_result(
    conn: sqlite3.Connection,
    run_id: int,
    prompt_id: int,
    prompt_order: int,
    prompt_text: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO prompt_results(run_id, prompt_id, prompt_order, prompt_text)
           VALUES (?, ?, ?, ?)""",
        (run_id, prompt_id, prompt_order, prompt_text),
    )
    conn.commit()
    return cur.lastrowid


def update_prompt_result(
    conn: sqlite3.Connection,
    result_id: int,
    status: str,
    result_text: Optional[str] = None,
    result_json: Optional[str] = None,
    error_message: Optional[str] = None,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
):
    conn.execute(
        """UPDATE prompt_results SET status=?, result_text=?, result_json=?,
           error_message=?, cost_usd=?, duration_ms=? WHERE id=?""",
        (status, result_text, result_json, error_message, cost_usd, duration_ms, result_id),
    )
    conn.commit()


def get_prompt_results(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM prompt_results WHERE run_id=? ORDER BY prompt_order",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def purge_old_runs(conn: sqlite3.Connection, retention_days: int):
    conn.execute(
        """DELETE FROM runs WHERE finished_at < datetime('now', ? || ' days')""",
        (f"-{retention_days}",),
    )
    conn.commit()
