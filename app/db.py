import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.models import Run, RunStatus, Step, StepStatus, TraceEvent

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
        self,
        employee_id: str,
        step_names: list[str],
        max_steps: int = 20,
        max_tool_calls: int = 40,
    ) -> Run:
        """Insert the run and all its step rows in one transaction."""
        run_id = str(uuid.uuid4())
        ts = now()
        try:
            await self.conn.execute(
                "INSERT INTO runs (id, employee_id, status, max_steps, max_tool_calls,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, employee_id, RunStatus.PENDING.value, max_steps, max_tool_calls, ts, ts),
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
