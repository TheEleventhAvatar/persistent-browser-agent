"""
SQLite-backed memory store for the agent.

This is the whole "remembers what it already did" mechanism. Nothing fancy —
a tasks table, a steps table (the flattened task graph), and a scratchpad
table for free-text working memory. Resume just means: load the row, skip
what's `done`, continue from what isn't.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).parent.parent / "memory.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Step:
    step_id: str
    task_id: str
    step_index: int
    description: str
    status: str = "pending"
    result: Optional[dict] = None
    error: Optional[str] = None
    attempts: int = 0


@dataclass
class Task:
    task_id: str
    goal: str
    status: str = "pending"
    steps: list[Step] = field(default_factory=list)
    scratchpad: str = ""
    cache_ttl_seconds: Optional[int] = None


class MemoryStore:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA_PATH.read_text())

    # ---------- task lifecycle ----------

    def create_task(
        self, goal: str, step_descriptions: list[str], cache_ttl_seconds: Optional[int] = None
    ) -> Task:
        task_id = uuid.uuid4().hex[:8]
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, goal, status, cache_ttl_seconds, "
                "created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
                (task_id, goal, cache_ttl_seconds, now, now),
            )
            conn.execute(
                "INSERT INTO scratchpad (task_id, content, updated_at) VALUES (?, '', ?)",
                (task_id, now),
            )
            for i, desc in enumerate(step_descriptions):
                conn.execute(
                    "INSERT INTO steps (step_id, task_id, step_index, description, "
                    "status, updated_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                    (f"{task_id}-{i}", task_id, i, desc, now),
                )
        return self.load_task(task_id)

    def load_task(self, task_id: str) -> Task:
        with self._conn() as conn:
            trow = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if trow is None:
                raise KeyError(f"no task with id {task_id}")
            srows = conn.execute(
                "SELECT * FROM steps WHERE task_id = ? ORDER BY step_index",
                (task_id,),
            ).fetchall()
            pad = conn.execute(
                "SELECT content FROM scratchpad WHERE task_id = ?", (task_id,)
            ).fetchone()

        steps = [
            Step(
                step_id=r["step_id"],
                task_id=r["task_id"],
                step_index=r["step_index"],
                description=r["description"],
                status=r["status"],
                result=json.loads(r["result"]) if r["result"] else None,
                error=r["error"],
                attempts=r["attempts"],
            )
            for r in srows
        ]
        return Task(
            task_id=trow["task_id"],
            goal=trow["goal"],
            status=trow["status"],
            steps=steps,
            scratchpad=pad["content"] if pad else "",
            cache_ttl_seconds=trow["cache_ttl_seconds"],
        )

    def list_tasks(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT task_id, goal, status, created_at, updated_at FROM tasks "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_task_status(self, task_id: str, status: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, _now(), task_id),
            )

    # ---------- step lifecycle ----------

    def mark_step_in_progress(self, step_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE steps SET status = 'in_progress', attempts = attempts + 1, "
                "updated_at = ? WHERE step_id = ?",
                (_now(), step_id),
            )

    def mark_step_done(self, step_id: str, result: Any):
        with self._conn() as conn:
            conn.execute(
                "UPDATE steps SET status = 'done', result = ?, error = NULL, "
                "updated_at = ? WHERE step_id = ?",
                (json.dumps(result), _now(), step_id),
            )

    def mark_step_failed(self, step_id: str, error: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE steps SET status = 'failed', error = ?, updated_at = ? "
                "WHERE step_id = ?",
                (error, _now(), step_id),
            )

    # ---------- scratchpad (working memory) ----------

    def append_scratchpad(self, task_id: str, note: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT content FROM scratchpad WHERE task_id = ?", (task_id,)
            ).fetchone()
            updated = (row["content"] + "\n" + note).strip() if row else note
            conn.execute(
                "UPDATE scratchpad SET content = ?, updated_at = ? WHERE task_id = ?",
                (updated, _now(), task_id),
            )

    def replace_scratchpad(self, task_id: str, content: str):
        """Used after LLM-driven summarization/compression of the scratchpad."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE scratchpad SET content = ?, updated_at = ? WHERE task_id = ?",
                (content, _now(), task_id),
            )

    # ---------- facts (cross-task memory) ----------

    def get_fact(self, fact_key: str) -> Optional[dict]:
        """Returns {"value": ..., "updated_at": ..., "source_task_id": ...} or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value, updated_at, source_task_id FROM facts WHERE fact_key = ?",
                (fact_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "value": json.loads(row["value"]),
            "updated_at": row["updated_at"],
            "source_task_id": row["source_task_id"],
        }

    def set_fact(self, fact_key: str, value: Any, source_task_id: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO facts (fact_key, value, source_task_id, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(fact_key) DO UPDATE SET "
                "value = excluded.value, source_task_id = excluded.source_task_id, "
                "updated_at = excluded.updated_at",
                (fact_key, json.dumps(value), source_task_id, _now()),
            )

    def fact_age_seconds(self, fact_key: str) -> Optional[float]:
        fact = self.get_fact(fact_key)
        if fact is None:
            return None
        updated = datetime.fromisoformat(fact["updated_at"])
        return (datetime.now(timezone.utc) - updated).total_seconds()

    # ---------- resume helpers ----------

    def next_pending_step(self, task_id: str) -> Optional[Step]:
        task = self.load_task(task_id)
        for step in task.steps:
            # "in_progress" is included here on purpose: if the process was
            # killed (Ctrl+C, crash, power loss) mid-step, that step is left
            # at "in_progress" with nothing actually running anymore. On
            # resume there's no live process to finish it, so it must be
            # treated as resumable — otherwise it's silently skipped forever
            # and the task graph "completes" with a gap in it.
            if step.status in ("pending", "failed", "in_progress"):
                return step
        return None

    def progress_summary(self, task_id: str) -> str:
        task = self.load_task(task_id)
        done = sum(1 for s in task.steps if s.status == "done")
        total = len(task.steps)
        return f"{done}/{total} sub-goals already done"