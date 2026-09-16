import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.models import (
    APPROVAL_PAUSE_REASONS,
    EXTERNAL_PAUSE_REASONS,
    Run,
    RunStatus,
    Step,
    StepStatus,
    TraceEvent,
    InvocationStatus,
)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(value: str | None) -> dict | None:
    return json.loads(value) if value is not None else None


class Database:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    @classmethod
    async def connect(cls, path: str) -> "Database":
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript(SCHEMA_PATH.read_text())
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self.conn.close()

    # Runs and steps

    async def create_run(
        self, employee_id: str, step_names: list[str], max_tool_calls: int = 40
    ) -> Run:
        """Insert the run and all its step rows in one transaction."""
        run_id = str(uuid.uuid4())
        ts = now()
        try:
            await self.conn.execute(
                "INSERT INTO runs (id, employee_id, status, max_tool_calls,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, employee_id, RunStatus.PENDING.value, max_tool_calls, ts, ts),
            )
            await self.conn.executemany(
                "INSERT INTO steps (run_id, idx, name, status) VALUES (?, ?, ?, ?)",
                [
                    (run_id, idx, name, StepStatus.PENDING.value)
                    for idx, name in enumerate(step_names, start=1)
                ],
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise
        run = await self.load_run(run_id)
        assert run is not None
        return run

    async def load_run(self, run_id: str) -> Run | None:
        async with self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
        return Run(**dict(row)) if row else None

    async def load_steps(self, run_id: str) -> list[Step]:
        async with self.conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY idx", (run_id,)
        ) as cur:
            rows = await cur.fetchall()

        res = []
        for row in rows:
            step_dict = dict(row)
            step_dict["output"] = _loads(row["output"])
            res.append(Step(**step_dict))
        return res

    # Effects: the fake outside world. Duplicate keys never create a second row.

    async def record_effect(self, idempotency_key: str, kind: str, payload: dict) -> tuple[dict, bool]:
        """Returns the stored effect and whether this call created it."""
        cur = await self.conn.execute(
            "INSERT INTO effects (idempotency_key, kind, payload, created_at)"
            " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (idempotency_key, kind, json.dumps(payload), now()),
        )
        created = cur.rowcount == 1
        await self.conn.commit()

        stored = await self.load_effect(idempotency_key)
        assert stored is not None
        return stored, created

    async def load_effect(self, idempotency_key: str) -> dict | None:
        async with self.conn.execute(
            "SELECT payload FROM effects WHERE idempotency_key = ?", (idempotency_key,)
        ) as cur:
            row = await cur.fetchone()
        return _loads(row["payload"]) if row else None

    async def load_effects_of_kind(self, kind: str) -> list[dict]:
        async with self.conn.execute(
            "SELECT payload FROM effects WHERE kind = ? ORDER BY created_at, idempotency_key",
            (kind,),
        ) as cur:
            rows = await cur.fetchall()

        res = []
        for row in rows:
            res.append(_loads(row["payload"]))
        return res

    # Run progress

    async def claim_run(self, run_id: str) -> bool:
        """One worker, so the only guard needed is that the run is still pending."""
        cur = await self.conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (RunStatus.RUNNING.value, now(), run_id, RunStatus.PENDING.value),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def complete_run(self, run_id: str) -> None:
        await self._set_run_status(run_id, RunStatus.COMPLETED)

    async def fail_run(self, run_id: str, error: str) -> None:
        await self._set_run_status(run_id, RunStatus.FAILED, error=error)

    async def pause_run(self, run_id: str, reason: str) -> None:
        await self._set_run_status(run_id, RunStatus.PAUSED, pause_reason=reason)

    async def _set_run_status(
        self, run_id: str, status: RunStatus, error: str | None = None, pause_reason: str | None = None
    ) -> None:
        await self.conn.execute(
            "UPDATE runs SET status = ?, error = ?, pause_reason = ?, updated_at = ? WHERE id = ?",
            (status.value, error, pause_reason, now(), run_id),
        )
        await self.conn.commit()

    # Operator decisions. Each is a conditional update: rowcount 0 means it was a no-op.

    async def approve_run(self, run_id: str) -> bool:
        reasons = ", ".join("?" for _ in APPROVAL_PAUSE_REASONS)
        cur = await self.conn.execute(
            "UPDATE runs SET status = 'pending', pause_reason = NULL,"
            " approval_decision = 'approved', updated_at = ?"
            f" WHERE id = ? AND status = 'paused' AND pause_reason IN ({reasons})",
            (now(), run_id, *APPROVAL_PAUSE_REASONS),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def reject_run(self, run_id: str, error: str) -> bool:
        reasons = ", ".join("?" for _ in APPROVAL_PAUSE_REASONS)
        cur = await self.conn.execute(
            "UPDATE runs SET status = 'failed', pause_reason = NULL,"
            " approval_decision = 'rejected', error = ?, updated_at = ?"
            f" WHERE id = ? AND status = 'paused' AND pause_reason IN ({reasons})",
            (error, now(), run_id, *APPROVAL_PAUSE_REASONS),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def resume_run(self, run_id: str) -> bool:
        reasons = ", ".join("?" for _ in EXTERNAL_PAUSE_REASONS)
        cur = await self.conn.execute(
            "UPDATE runs SET status = 'pending', pause_reason = NULL, updated_at = ?"
            f" WHERE id = ? AND status = 'paused' AND pause_reason IN ({reasons})",
            (now(), run_id, *EXTERNAL_PAUSE_REASONS),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def cancel_run(self, run_id: str) -> bool:
        cur = await self.conn.execute(
            "UPDATE runs SET status = 'cancelled', pause_reason = NULL, updated_at = ?"
            " WHERE id = ? AND status IN ('pending', 'running', 'paused')",
            (now(), run_id),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def clear_approval(self, run_id: str) -> None:
        """A gate consumes its decision, so the next gate cannot inherit it."""
        await self.conn.execute(
            "UPDATE runs SET approval_decision = NULL, updated_at = ? WHERE id = ?",
            (now(), run_id),
        )
        await self.conn.commit()

    async def active_run_for_employee(self, employee_id: str) -> Run | None:
        async with self.conn.execute(
            "SELECT * FROM runs WHERE employee_id = ? AND status IN ('pending','running','paused')"
            " ORDER BY created_at DESC LIMIT 1",
            (employee_id,),
        ) as cur:
            row = await cur.fetchone()
        return Run(**dict(row)) if row else None

    async def list_runs(self, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Run]:
        sql = "SELECT * FROM runs"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()

        res = []
        for row in rows:
            res.append(Run(**dict(row)))
        return res

    # Step progress

    async def start_step(self, run_id: str, idx: int) -> None:
        await self.conn.execute(
            "UPDATE steps SET status = ?, attempts = attempts + 1, started_at = ?"
            " WHERE run_id = ? AND idx = ?",
            (StepStatus.RUNNING.value, now(), run_id, idx),
        )
        await self.conn.commit()

    async def complete_step(
        self, run_id: str, idx: int, output: dict | None, skipped: bool = False
    ) -> None:
        """Marking the step done and advancing the run is one transaction: the checkpoint."""
        status = StepStatus.SKIPPED if skipped else StepStatus.COMPLETED
        ts = now()
        try:
            await self.conn.execute(
                "UPDATE steps SET status = ?, output = ?, completed_at = ? WHERE run_id = ? AND idx = ?",
                (status.value, json.dumps(output) if output else None, ts, run_id, idx),
            )
            await self.conn.execute(
                "UPDATE runs SET current_step = ?, updated_at = ? WHERE id = ?", (idx + 1, ts, run_id)
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    async def save_step_output(self, run_id: str, idx: int, output: dict) -> None:
        """Used when a step pauses: what it decided survives the wait."""
        await self.conn.execute(
            "UPDATE steps SET output = ? WHERE run_id = ? AND idx = ?",
            (json.dumps(output), run_id, idx),
        )
        await self.conn.commit()

    async def reset_step(self, run_id: str, idx: int) -> None:
        await self.conn.execute(
            "UPDATE steps SET status = ? WHERE run_id = ? AND idx = ?",
            (StepStatus.PENDING.value, run_id, idx),
        )
        await self.conn.commit()

    async def fail_step(self, run_id: str, idx: int, error: str) -> None:
        await self.conn.execute(
            "UPDATE steps SET status = ?, error = ?, completed_at = ? WHERE run_id = ? AND idx = ?",
            (StepStatus.FAILED.value, error, now(), run_id, idx),
        )
        await self.conn.commit()

    async def load_step_outputs(self, run_id: str) -> dict[int, dict]:
        async with self.conn.execute(
            "SELECT idx, output FROM steps WHERE run_id = ? AND output IS NOT NULL", (run_id,)
        ) as cur:
            rows = await cur.fetchall()

        res = {}
        for row in rows:
            res[row["idx"]] = _loads(row["output"])
        return res

    # Tool invocations: write-ahead intent for every side effect.

    async def load_invocation(self, idempotency_key: str) -> dict | None:
        async with self.conn.execute(
            "SELECT * FROM tool_invocations WHERE idempotency_key = ?", (idempotency_key,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None

        invocation = dict(row)
        invocation["args"] = _loads(row["args"]) or {}
        invocation["result"] = _loads(row["result"])
        return invocation

    async def begin_invocation(
        self, idempotency_key: str, run_id: str, step_idx: int, tool: str, args: dict
    ) -> None:
        ts = now()
        await self.conn.execute(
            "INSERT INTO tool_invocations (idempotency_key, run_id, step_idx, tool, args, status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (idempotency_key, run_id, step_idx, tool, json.dumps(args), InvocationStatus.IN_FLIGHT.value, ts, ts),
        )
        await self.conn.commit()

    async def finish_invocation(self, idempotency_key: str, result: dict) -> None:
        await self.conn.execute(
            "UPDATE tool_invocations SET status = ?, result = ?, updated_at = ? WHERE idempotency_key = ?",
            (InvocationStatus.SUCCEEDED.value, json.dumps(result), now(), idempotency_key),
        )
        await self.conn.commit()

    async def fail_invocation(self, idempotency_key: str) -> None:
        await self.conn.execute(
            "UPDATE tool_invocations SET status = ?, updated_at = ? WHERE idempotency_key = ?",
            (InvocationStatus.FAILED.value, now(), idempotency_key),
        )
        await self.conn.commit()

    async def delete_invocation(self, idempotency_key: str) -> None:
        await self.conn.execute(
            "DELETE FROM tool_invocations WHERE idempotency_key = ?", (idempotency_key,)
        )
        await self.conn.commit()

    # Learned role mappings: titles whose groups a human approved on an earlier run.

    async def save_role_groups(self, role: str, groups: list[str]) -> None:
        await self.conn.execute(
            "INSERT INTO role_groups (role, groups, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (role, json.dumps(groups), now()),
        )
        await self.conn.commit()

    async def load_role_groups(self, role: str) -> list[str] | None:
        async with self.conn.execute(
            "SELECT groups FROM role_groups WHERE role = ?", (role,)
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row["groups"]) if row else None

    async def load_all_role_groups(self) -> dict[str, list[str]]:
        async with self.conn.execute("SELECT role, groups FROM role_groups ORDER BY role") as cur:
            rows = await cur.fetchall()

        res = {}
        for row in rows:
            res[row["role"]] = json.loads(row["groups"])
        return res

    # Trace. Callers go through app.trace.append, which redacts first.

    async def insert_trace(
        self, run_id: str, step_idx: int | None, event: str, detail: dict | None
    ) -> None:
        await self.conn.execute(
            "INSERT INTO trace (run_id, step_idx, event, detail, at) VALUES (?, ?, ?, ?, ?)",
            (run_id, step_idx, event, json.dumps(detail) if detail is not None else None, now()),
        )
        await self.conn.commit()

    async def load_trace(self, run_id: str) -> list[TraceEvent]:
        async with self.conn.execute(
            "SELECT * FROM trace WHERE run_id = ? ORDER BY id", (run_id,)
        ) as cur:
            rows = await cur.fetchall()

        res = []
        for row in rows:
            row_dict = dict(row)
            row_dict["detail"] = _loads(row["detail"])
            res.append(TraceEvent(**row_dict))
        return res
