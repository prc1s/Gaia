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
