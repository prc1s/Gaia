from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from app import trace
from app.config import Settings, get_settings
from app.db import Database
from app.llm import build_llm
from app.steps import STEP_NAMES
from app.tools import FaultInjector, ToolBox
from app.worker import Worker


class CreateRunRequest(BaseModel):
    employee_id: str


def create_app(
    settings: Settings | None = None,
    faults: FaultInjector | None = None,
    llm=None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = await Database.connect(settings.db_path)
        worker = Worker(db, ToolBox(db, faults), llm or build_llm(settings), settings)
        await worker.start()

        app.state.db = db
        app.state.worker = worker
        app.state.settings = settings
        try:
            yield
        finally:
            await worker.stop()
            await db.close()

    app = FastAPI(title="GAIA onboarding orchestrator", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "llm_configured": settings.anthropic_api_key is not None,
            "queue_depth": app.state.worker.queue.qsize(),
        }

    @app.post("/runs", status_code=201)
    async def create_run(body: CreateRunRequest, response: Response) -> dict:
        db = app.state.db

        existing = await db.active_run_for_employee(body.employee_id)
        if existing is not None:
            response.status_code = 200
            return existing.model_dump()

        run = await db.create_run(
            body.employee_id, STEP_NAMES, settings.max_steps, settings.max_tool_calls
        )
        await trace.append(db, run.id, None, "run_created", {"employee_id": body.employee_id})
        await app.state.worker.submit(run.id)
        return run.model_dump()

    @app.get("/runs")
    async def list_runs(status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        runs = await app.state.db.list_runs(status, limit, offset)

        res = []
        for run in runs:
            res.append(run.model_dump())
        return {"runs": res}

    @app.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        db = app.state.db
        run = await db.load_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="unknown run")

        steps = await db.load_steps(run_id)
        res = []
        for step in steps:
            res.append(step.model_dump())
        return {"run": run.model_dump(), "steps": res}

    @app.get("/runs/{run_id}/trace")
    async def get_trace(run_id: str) -> dict:
        db = app.state.db
        if await db.load_run(run_id) is None:
            raise HTTPException(status_code=404, detail="unknown run")

        events = await db.load_trace(run_id)
        res = []
        for event in events:
            res.append(event.model_dump())
        return {"trace": res}

    return app


app = create_app()
