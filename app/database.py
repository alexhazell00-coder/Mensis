"""SQLite persistence for task state and append-only activity events."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


TASK_STATUSES = frozenset(
    {
        "queued",
        "planning",
        "running",
        "verifying",
        "completed",
        "blocked",
        "failed",
        "cancelled",
    }
)
ACTIVE_STATUSES = frozenset({"planning", "running", "verifying"})
TERMINAL_STATUSES = frozenset({"completed", "blocked", "failed", "cancelled"})
_ALLOWED_TRANSITIONS = {
    "queued": frozenset({"planning", "cancelled"}),
    "planning": frozenset({"running", "blocked", "failed", "cancelled"}),
    "running": frozenset({"verifying", "blocked", "failed", "cancelled"}),
    "verifying": frozenset({"completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "blocked": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_json(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class InvalidTransition(ValueError):
    """Raised when a task lifecycle transition is not allowed."""


class TaskStore:
    """Small SQLite repository; connections are short-lived and thread-safe."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            str(self.database_path), timeout=10.0, isolation_level="IMMEDIATE"
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    request TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'queued', 'planning', 'running', 'verifying',
                        'completed', 'blocked', 'failed', 'cancelled'
                    )),
                    plan_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT,
                    verification TEXT NOT NULL DEFAULT 'pending' CHECK (
                        verification IN ('pending', 'verified', 'unverified', 'not_applicable')
                    ),
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status_created
                    ON tasks(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_task_events_task_id
                    ON task_events(task_id, id);
                PRAGMA user_version = 1;
                """
            )

    def create_task(self, request: str) -> str:
        task_id = str(uuid.uuid4())
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO tasks (id, request, status, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?)""",
                (task_id, request, now, now),
            )
            self._insert_event(
                connection,
                task_id,
                "received",
                "Task received and added to the local queue.",
                {},
                now,
            )
        return task_id

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO task_events (task_id, event_type, message, data_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                task_id,
                event_type,
                message,
                _encode_json(data or {}),
                timestamp or utc_now(),
            ),
        )

    def add_event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if exists is None:
                return
            self._insert_event(connection, task_id, event_type, message, data)
            connection.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (utc_now(), task_id)
            )

    def set_plan(self, task_id: str, steps: list[str]) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE tasks SET plan_json = ?, updated_at = ? WHERE id = ?",
                (_encode_json(steps), utc_now(), task_id),
            )

    def transition(
        self,
        task_id: str,
        new_status: str,
        *,
        result: dict[str, Any] | None = None,
        verification: str | None = None,
        error: str | None = None,
        message: str | None = None,
    ) -> bool:
        if new_status not in TASK_STATUSES:
            raise InvalidTransition(f"Unknown task status: {new_status}")
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return False
            old_status = row["status"]
            if new_status not in _ALLOWED_TRANSITIONS[old_status]:
                raise InvalidTransition(f"Cannot move task from {old_status} to {new_status}")
            started_at = now if new_status == "planning" else None
            finished_at = now if new_status in TERMINAL_STATUSES else None
            connection.execute(
                """UPDATE tasks
                   SET status = ?, updated_at = ?,
                       started_at = COALESCE(started_at, ?),
                       finished_at = COALESCE(?, finished_at),
                       result_json = COALESCE(?, result_json),
                       verification = COALESCE(?, verification),
                       error = ?,
                       cancel_requested = CASE WHEN ? = 'cancelled' THEN 0 ELSE cancel_requested END
                   WHERE id = ?""",
                (
                    new_status,
                    now,
                    started_at,
                    finished_at,
                    _encode_json(result) if result is not None else None,
                    verification,
                    error,
                    new_status,
                    task_id,
                ),
            )
            self._insert_event(
                connection,
                task_id,
                "status_changed",
                message or f"Task status changed from {old_status} to {new_status}.",
                {"from": old_status, "to": new_status},
                now,
            )
        return True

    def claim_queued_task(self, task_id: str) -> bool:
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """UPDATE tasks
                   SET status = 'planning', started_at = COALESCE(started_at, ?), updated_at = ?
                   WHERE id = ? AND status = 'queued'""",
                (now, now, task_id),
            )
            if cursor.rowcount != 1:
                return False
            self._insert_event(
                connection,
                task_id,
                "status_changed",
                "Task status changed from queued to planning.",
                {"from": "queued", "to": "planning"},
                now,
            )
            return True

    def get_task(self, task_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            task = self._row_to_task(row)
            if include_events:
                events = connection.execute(
                    """SELECT id, event_type, message, data_json, created_at
                       FROM task_events WHERE task_id = ? ORDER BY id""",
                    (task_id,),
                ).fetchall()
                task["events"] = [self._row_to_event(event) for event in events]
            return task

    def list_tasks(
        self, *, limit: int = 50, status: str | None = None
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM tasks WHERE status = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            return [self._row_to_task(row) for row in rows]

    def list_queued_ids(self) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id FROM tasks WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
            return [row["id"] for row in rows]

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return bool(row["cancel_requested"]) if row else True

    def request_cancel(self, task_id: str) -> tuple[bool, dict[str, Any] | None]:
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status, cancel_requested FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return False, None
            current = row["status"]
            if current == "queued":
                connection.execute(
                    """UPDATE tasks SET status = 'cancelled', finished_at = ?, updated_at = ?
                       WHERE id = ? AND status = 'queued'""",
                    (now, now, task_id),
                )
                self._insert_event(
                    connection,
                    task_id,
                    "cancelled",
                    "Task was cancelled before execution began.",
                    {},
                    now,
                )
                return True, self._get_task_in_connection(connection, task_id)
            if current in ACTIVE_STATUSES:
                if row["cancel_requested"]:
                    return True, self._get_task_in_connection(connection, task_id)
                connection.execute(
                    "UPDATE tasks SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                    (now, task_id),
                )
                self._insert_event(
                    connection,
                    task_id,
                    "cancel_requested",
                    "Cancellation requested; the current read-only operation will stop at a safe checkpoint.",
                    {},
                    now,
                )
                return True, self._get_task_in_connection(connection, task_id)
            return False, self._get_task_in_connection(connection, task_id)

    def resume_task(self, task_id: str) -> tuple[bool, dict[str, Any] | None]:
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status, attempt FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return False, None
            if row["status"] not in {"blocked", "failed"}:
                return False, self._get_task_in_connection(connection, task_id)
            previous_status = row["status"]
            connection.execute(
                """UPDATE tasks SET status = 'queued', result_json = NULL,
                       verification = 'pending', error = NULL, cancel_requested = 0,
                       attempt = attempt + 1, updated_at = ?, started_at = NULL,
                       finished_at = NULL, plan_json = '[]'
                   WHERE id = ?""",
                (now, task_id),
            )
            self._insert_event(
                connection,
                task_id,
                "resumed",
                f"Task was re-queued after attempt {row['attempt']} ended as {previous_status}.",
                {"previous_status": previous_status, "attempt": row["attempt"] + 1},
                now,
            )
            return True, self._get_task_in_connection(connection, task_id)

    def recover_interrupted_tasks(self) -> int:
        """Requeue interrupted work; the current MVP exposes read-only tools only."""
        now = utc_now()
        recovered = 0
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, status, cancel_requested FROM tasks WHERE status IN ('planning', 'running', 'verifying')"
            ).fetchall()
            for row in rows:
                if row["cancel_requested"]:
                    new_status = "cancelled"
                    message = "Task was cancelled while MENSIS was restarting."
                else:
                    new_status = "queued"
                    message = "Interrupted read-only work was safely re-queued after restart."
                finished_at = now if new_status == "cancelled" else None
                connection.execute(
                    """UPDATE tasks SET status = ?, updated_at = ?, finished_at = ?,
                           cancel_requested = 0 WHERE id = ?""",
                    (new_status, now, finished_at, row["id"]),
                )
                self._insert_event(
                    connection,
                    row["id"],
                    "recovered",
                    message,
                    {"from": row["status"], "to": new_status},
                    now,
                )
                recovered += 1
        return recovered

    def _get_task_in_connection(
        self, connection: sqlite3.Connection, task_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return self._row_to_task(row) if row is not None else None

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "request": row["request"],
            "status": row["status"],
            "plan": _decode_json(row["plan_json"], []),
            "result": _decode_json(row["result_json"], None),
            "verification": row["verification"],
            "error": row["error"],
            "cancel_requested": bool(row["cancel_requested"]),
            "attempt": row["attempt"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "type": row["event_type"],
            "message": row["message"],
            "data": _decode_json(row["data_json"], {}),
            "created_at": row["created_at"],
        }
