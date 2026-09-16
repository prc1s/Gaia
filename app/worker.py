import asyncio

from app import trace
from app.models import RunStatus
from app.orchestrator import execute_run


class Worker:
    """The queue holds nothing durable: position lives in runs.current_step."""

    def __init__(self, db, tools, llm, settings) -> None:
        self.db = db
        self.tools = tools
        self.llm = llm
        self.settings = settings
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self.task is None:
            return

        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None

    async def submit(self, run_id: str) -> None:
        await self.queue.put(run_id)

    async def sweep(self) -> None:
        """Rebuild the queue from the database. Paused runs are left alone."""
        await self._reconcile_invocations()

        for run_id in await self.db.reclaim_running_runs():
            await trace.append(self.db, run_id, None, "run_reclaimed")

        for run_id in await self.db.load_run_ids_by_status(RunStatus.PENDING):
            await self.submit(run_id)

    async def _reconcile_invocations(self) -> None:
        """An effect row is the proof. Its absence is proof the tool never landed."""
        for invocation in await self.db.load_in_flight_invocations():
            key = invocation["idempotency_key"]
            effect = await self.db.load_effect(key)

            if effect is not None:
                await self.db.finish_invocation(key, effect)
                outcome = "effect_landed"
            else:
                await self.db.fail_invocation(key)
                outcome = "effect_absent"

            await trace.append(
                self.db,
                invocation["run_id"],
                invocation["step_idx"],
                "invocation_reconciled",
                {"tool": invocation["tool"], "outcome": outcome},
            )

    async def _consume(self) -> None:
        while True:
            run_id = await self.queue.get()
            try:
                if await self.db.claim_run(run_id):
                    await execute_run(self.db, self.tools, self.llm, self.settings, run_id)
            except Exception as exc:
                await self.db.fail_run(run_id, f"worker: {type(exc).__name__}")
                await trace.append(
                    self.db, run_id, None, "run_failed", {"error": type(exc).__name__}
                )
            finally:
                self.queue.task_done()
