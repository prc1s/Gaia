import pytest

from app.db import Database

STEP_NAMES = [
    "fetch_employee",
    "create_account",
    "send_credentials",
    "resolve_groups",
    "propose_equipment",
    "equipment_gate",
    "order_laptop",
    "notify_manager",
]


@pytest.fixture
async def db(tmp_path):
    database = await Database.connect(str(tmp_path / "test.db"))
    yield database
    await database.close()
