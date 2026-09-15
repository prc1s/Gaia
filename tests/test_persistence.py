from app import trace
from app.models import RunStatus, StepStatus
from tests.conftest import STEP_NAMES


async def test_pragmas(db):
    async with db.conn.execute("PRAGMA journal_mode") as cur:
        assert (await cur.fetchone())[0] == "wal"
    async with db.conn.execute("PRAGMA foreign_keys") as cur:
        assert (await cur.fetchone())[0] == 1


async def test_create_and_load_run(db):
    run = await db.create_run("emp-1", STEP_NAMES)
    loaded = await db.load_run(run.id)
    assert loaded == run
    assert loaded.employee_id == "emp-1"
    assert loaded.status == RunStatus.PENDING
    assert loaded.current_step == 1
    assert loaded.max_steps == 20 and loaded.max_tool_calls == 40


async def test_load_unknown_run(db):
    assert await db.load_run("nope") is None


async def test_steps_created_upfront(db):
    run = await db.create_run("emp-1", STEP_NAMES)
    steps = await db.load_steps(run.id)
    assert [s.idx for s in steps] == list(range(1, 9))
    assert [s.name for s in steps] == STEP_NAMES
    assert all(s.status == StepStatus.PENDING and s.attempts == 0 for s in steps)


async def test_trace_appends_in_order(db):
    run = await db.create_run("emp-1", STEP_NAMES)
    await trace.append(db, run.id, None, "run_created")
    await trace.append(db, run.id, 1, "step_started")
    await trace.append(db, run.id, 1, "step_completed", {"attempts": 1})
    events = await db.load_trace(run.id)
    assert [e.event for e in events] == ["run_created", "step_started", "step_completed"]
    assert events[2].detail == {"attempts": 1}


async def test_trace_redacts_detail(db):
    run = await db.create_run("emp-1", STEP_NAMES)
    await trace.append(db, run.id, 3, "tool_called", {"setup_link": "https://x/abc", "sent": True})
    [event] = await db.load_trace(run.id)
    assert event.detail == {"sent": True}
    async with db.conn.execute("SELECT detail FROM trace") as cur:
        assert "abc" not in (await cur.fetchone())[0]
