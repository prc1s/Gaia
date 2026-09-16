import asyncio

from app import trace
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
