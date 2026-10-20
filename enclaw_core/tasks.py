"""Durable local task queue with explicit state transitions and append-only events."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_STATES = {
    "queued",
    "planning",
    "running",
    "validating",
    "awaiting_approval",
    "integrating",
    "completed",
    "failed",
    "cancelled",
}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
TRANSITIONS = {
    "queued": {"planning", "cancelled"},
    "planning": {"running", "failed", "cancelled"},
    "running": {"validating", "awaiting_approval", "failed", "cancelled"},
    "validating": {"running", "awaiting_approval", "integrating", "failed", "cancelled"},
    "awaiting_approval": {"running", "integrating", "cancelled", "failed"},
    "integrating": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": {"queued", "cancelled"},
    "cancelled": set(),
}


class TaskStoreError(RuntimeError):
    """Raised when a task operation is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskStore:
    def __init__(self, database_path: str | Path):
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    branch TEXT,
                    worktree TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS tasks_project_status
                    ON tasks(project_id, status, created_at);
                CREATE TABLE IF NOT EXISTS task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_events_task
                    ON task_events(task_id, event_id);
                CREATE TABLE IF NOT EXISTS worker_leases (
                    lease_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    acquired_epoch REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS worker_leases_project
                    ON worker_leases(project_id, acquired_epoch);
            """)

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["cancellation_requested"] = bool(result["cancellation_requested"])
        result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
        return result

    def _event(
        self,
        db: sqlite3.Connection,
        task_id: str,
        actor: str,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        db.execute(
            "INSERT INTO task_events(task_id, at, actor, event_type, details_json) VALUES (?, ?, ?, ?, ?)",
            (task_id, _now(), actor, event_type, json.dumps(details, ensure_ascii=False)),
        )

    def create(self, project_id: str, title: str, description: str, actor: str = "operator") -> dict[str, Any]:
        project_id, title, description = project_id.strip(), title.strip(), description.strip()
        if not project_id or not title or not description:
            raise TaskStoreError("project_id, title, and description are required.")
        if len(title) > 200 or len(description) > 20000:
            raise TaskStoreError("Title or description exceeds the configured safety limit.")
        task_id = uuid.uuid4().hex[:12]
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO tasks(task_id, project_id, title, description, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (task_id, project_id, title, description, now, now),
            )
            self._event(db, task_id, actor, "task.created", {"project_id": project_id, "title": title})
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            db.commit()
        return self._task(row)

    def get(self, task_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskStoreError(f"Unknown task: {task_id}")
        return self._task(row)

    def list(self, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise TaskStoreError("limit must be between 1 and 200.")
        with self._connect() as db:
            if project_id:
                rows = db.execute(
                    "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._task(row) for row in rows]

    def transition(
        self,
        task_id: str,
        new_status: str,
        *,
        actor: str,
        details: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if new_status not in TASK_STATES:
            raise TaskStoreError(f"Unknown task status: {new_status}")
        details = details or {}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                db.rollback()
                raise TaskStoreError(f"Unknown task: {task_id}")
            current = row["status"]
            if new_status not in TRANSITIONS[current]:
                db.rollback()
                raise TaskStoreError(f"Invalid task transition: {current} -> {new_status}")
            now = _now()
            result_json = json.dumps(result, ensure_ascii=False) if result is not None else row["result_json"]
            db.execute(
                "UPDATE tasks SET status = ?, updated_at = ?, result_json = ? WHERE task_id = ?",
                (new_status, now, result_json, task_id),
            )
            self._event(db, task_id, actor, "task.transition", {
                **details, "from": current, "to": new_status,
            })
            updated = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            db.commit()
        return self._task(updated)

    def acquire_project_slot(self, project_id: str, limit: int, *, lease_seconds: int = 7200) -> str | None:
        """Atomically reserve one project worker slot across processes sharing this SQLite DB."""
        if not project_id or not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise TaskStoreError("project_id and a non-negative worker limit are required.")
        if limit == 0:
            return None
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            stale = db.execute(
                "SELECT lease_id, pid, acquired_epoch FROM worker_leases"
            ).fetchall()
            for lease in stale:
                alive = True
                try:
                    os.kill(int(lease["pid"]), 0)
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
                if not alive or now - float(lease["acquired_epoch"]) > lease_seconds:
                    db.execute("DELETE FROM worker_leases WHERE lease_id=?", (lease["lease_id"],))
            active = db.execute(
                "SELECT COUNT(*) FROM worker_leases WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            if active >= limit:
                db.rollback()
                return None
            token = uuid.uuid4().hex
            db.execute(
                "INSERT INTO worker_leases(lease_id, project_id, pid, acquired_epoch) VALUES (?, ?, ?, ?)",
                (token, project_id, os.getpid(), now),
            )
            db.commit()
        return token

    def release_project_slot(self, lease_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM worker_leases WHERE lease_id=?", (lease_id,))

    def retry_failed(self, task_id: str, *, max_retries: int, actor: str = "operator") -> dict[str, Any]:
        """Requeue a failed task explicitly, within the caller-supplied project retry budget."""
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            raise TaskStoreError("max_retries must be a non-negative integer.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                db.rollback()
                raise TaskStoreError(f"Unknown task: {task_id}")
            if row["status"] != "failed":
                db.rollback()
                raise TaskStoreError("Only failed tasks can be retried.")
            if row["attempts"] >= max_retries:
                db.rollback()
                raise TaskStoreError(f"Retry budget exhausted ({row['attempts']}/{max_retries}).")
            now = _now()
            db.execute(
                "UPDATE tasks SET status='queued', attempts=attempts+1, cancellation_requested=0, "
                "updated_at=?, result_json=NULL WHERE task_id=?",
                (now, task_id),
            )
            self._event(db, task_id, actor, "task.retry_requested", {
                "previous_attempts": row["attempts"], "max_retries": max_retries,
                "from": "failed", "to": "queued",
            })
            updated = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            db.commit()
        return self._task(updated)

    def request_cancel(self, task_id: str, actor: str = "operator") -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                db.rollback()
                raise TaskStoreError(f"Unknown task: {task_id}")
            if row["status"] in TERMINAL_STATES:
                db.rollback()
                raise TaskStoreError(f"Task is already terminal: {row['status']}")
            now = _now()
            db.execute(
                "UPDATE tasks SET cancellation_requested = 1, updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            self._event(db, task_id, actor, "task.cancel_requested", {"status": row["status"]})
            updated = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            db.commit()
        return self._task(updated)

    def events(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if exists is None:
                raise TaskStoreError(f"Unknown task: {task_id}")
            rows = db.execute(
                "SELECT event_id, task_id, at, actor, event_type, details_json "
                "FROM task_events WHERE task_id = ? ORDER BY event_id",
                (task_id,),
            ).fetchall()
        return [
            {**dict(row), "details": json.loads(row["details_json"])}
            for row in rows
        ]
