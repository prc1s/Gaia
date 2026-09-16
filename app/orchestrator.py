from app import trace
from app.models import InvocationStatus
from app.redaction import redact
from app.steps import STEPS


class RunContext:
    """Everything a step may touch. A step never reaches outside this."""

    def __init__(self, run, db, tools, llm, settings, outputs, step=None) -> None:
        self.run = run
        self.db = db
        self.tools = tools
        self.llm = llm
        self.settings = settings
        self.outputs = outputs
        self.step = step

    def step_output(self, idx: int) -> dict:
        return self.outputs.get(idx, {})

    async def save_output(self, output: dict) -> None:
        """Persist a decision before pausing, so resuming does not recompute it."""
        await self.db.save_step_output(self.run.id, self.step.idx, output)
        self.outputs[self.step.idx] = output

    async def consume_approval(self) -> str | None:
        """Read and clear: one decision approves one gate."""
        decision = self.run.approval_decision
        if decision is not None:
            await self.db.clear_approval(self.run.id)
            self.run.approval_decision = None
        return decision

    async def emit(self, event: str, detail: dict | None = None) -> None:
        await trace.append(self.db, self.run.id, self.step.idx, event, detail)

    def key_for(self, tool: str) -> str:
        return f"{self.run.id}:{self.step.idx}:{tool.split('.')[-1]}"

    async def invocation(self, tool: str) -> dict | None:
        return await self.db.load_invocation(self.key_for(tool))

    async def forget_invocation(self, tool: str) -> None:
        await self.db.delete_invocation(self.key_for(tool))

    async def call(self, tool: str, **args):
        """Read-only call: nothing to reconcile, so no invocation row."""
        await self.emit("tool_called", {"tool": tool})
        result = await self.tools.dispatch(tool, self.step.allowed_tools, **args)
        await self.emit("tool_succeeded", {"tool": tool})
        return result

    async def call_effect(self, tool: str, record: dict | None = None, **args):
        """Write-ahead intent, idempotent receiver, then record the result.

        `record` replaces the args written to the invocation row, for calls whose
        arguments carry a secret.
        """
        key = self.key_for(tool)
        existing = await self.db.load_invocation(key)

        if existing and existing["status"] == InvocationStatus.SUCCEEDED:
            return existing["result"]

        if existing is None:
            stored = record if record is not None else args
            await self.db.begin_invocation(key, self.run.id, self.step.idx, tool, redact(stored))

        await self.emit("tool_called", {"tool": tool})
        try:
            result = await self.tools.dispatch(
                tool, self.step.allowed_tools, idempotency_key=key, **args
            )
        except Exception as exc:
            await self.db.fail_invocation(key)
            await self.emit("tool_failed", {"tool": tool, "error": type(exc).__name__})
            raise

        await self.db.finish_invocation(key, result)
        await self.emit("tool_succeeded", {"tool": tool})
        return result


async def execute_run(db, tools, llm, settings, run_id: str) -> None:
    """Walk steps until the run pauses, fails, or completes."""
    run = await db.load_run(run_id)
    outputs = await db.load_step_outputs(run_id)
    await trace.append(db, run_id, None, "run_claimed")

    while run.current_step <= len(STEPS):
        step = STEPS[run.current_step - 1]
        ctx = RunContext(run, db, tools, llm, settings, outputs, step)

        await db.start_step(run_id, step.idx)
        await trace.append(db, run_id, step.idx, "step_started", {"name": step.name})

        try:
            result = await step.fn(ctx)
        except Exception as exc:
            error = f"{type(exc).__name__}: step {step.idx} {step.name}"
            await db.fail_step(run_id, step.idx, error)
            await trace.append(db, run_id, step.idx, "step_failed", {"error": type(exc).__name__})
            await db.fail_run(run_id, error)
            await trace.append(db, run_id, None, "run_failed", {"error": error})
            return

        if result.pause:
            await db.reset_step(run_id, step.idx)
            await db.pause_run(run_id, result.pause)
            await trace.append(db, run_id, step.idx, "run_paused", {"reason": result.pause})
            return

        await db.complete_step(run_id, step.idx, result.output, result.skipped)
        outputs[step.idx] = result.output or {}
        await trace.append(
            db, run_id, step.idx, "step_skipped" if result.skipped else "step_completed"
        )
        run.current_step += 1

    await db.complete_run(run_id)
    await trace.append(db, run_id, None, "run_completed")
