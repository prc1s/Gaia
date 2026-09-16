import pytest

from app.errors import (
    AddressTakenError,
    GuardrailViolation,
    PermanentToolError,
    TransientToolError,
)
from app.tools import FaultInjector, ToolBox, directory, mail, procurement

DOMAIN = "gaia.sa"


async def effect_count(db) -> int:
    async with db.conn.execute("SELECT COUNT(*) FROM effects") as cur:
        return (await cur.fetchone())[0]


async def make_account(db, address: str, key: str = "k-account") -> dict:
    return await directory.create_account(db, address, key)


# Effects


async def test_duplicate_key_creates_one_effect(db):
    first = await mail.send(db, "a@b.com", "hi", "body", "run-1:3:send")
    second = await mail.send(db, "a@b.com", "hi", "body", "run-1:3:send")
    assert first == second
    assert await effect_count(db) == 1


async def test_duplicate_key_keeps_the_original_payload(db):
    await mail.send(db, "a@b.com", "first", "body", "run-1:3:send")
    again = await mail.send(db, "a@b.com", "second", "body", "run-1:3:send")
    assert again["subject"] == "first"
    assert await effect_count(db) == 1


async def test_order_is_idempotent(db):
    key = "run-1:7:create_order"
    first = await procurement.create_order(db, "MacBook Pro 16", 8400, "a@gaia.sa", key)
    second = await procurement.create_order(db, "MacBook Air 13", 4000, "a@gaia.sa", key)
    assert first == second
    assert first["cost_sar"] == 8400
    assert await effect_count(db) == 1


async def test_record_effect_reports_creation(db):
    _, created = await db.record_effect("k", "order", {"order_id": "o-1"})
    assert created is True
    _, created_again = await db.record_effect("k", "order", {"order_id": "o-2"})
    assert created_again is False


# Dispatch and allowlist


async def test_dispatch_calls_the_tool(db):
    tools = ToolBox(db)
    employee = await tools.dispatch(
        "hr.get_employee", frozenset({"hr.get_employee"}), employee_id="emp-1001"
    )
    assert employee["last_name"] == "Almutairi"


async def test_tool_not_in_allowlist(db):
    tools = ToolBox(db)
    with pytest.raises(GuardrailViolation):
        await tools.dispatch("mail.send", frozenset({"hr.get_employee"}), employee_id="emp-1001")


async def test_unknown_tool(db):
    tools = ToolBox(db)
    with pytest.raises(GuardrailViolation):
        await tools.dispatch("mail.explode", frozenset({"mail.explode"}))


async def test_fault_injector_fails_exactly_n_times(db):
    tools = ToolBox(db, FaultInjector({"hr.get_employee": 2}))
    allowed = frozenset({"hr.get_employee"})

    for _ in range(2):
        with pytest.raises(TransientToolError):
            await tools.dispatch("hr.get_employee", allowed, employee_id="emp-1001")

    employee = await tools.dispatch("hr.get_employee", allowed, employee_id="emp-1001")
    assert employee["first_name"] == "Sara"


async def test_unknown_employee_is_permanent(db):
    tools = ToolBox(db)
    with pytest.raises(PermanentToolError):
        await tools.dispatch("hr.get_employee", frozenset({"hr.get_employee"}), employee_id="nope")


# Address normalisation


@pytest.mark.parametrize(
    "first, last, expected",
    [
        ("Sara", "Almutairi", "almutairi-sara"),
        ("Néstor", "González", "gonzalez-nestor"),
        ("Omar", "Al-Faraj", "al-faraj-omar"),
        ("Anne-Marie", "O'Neill", "oneill-anne-marie"),
        ("  Sara  ", "Al Mutairi", "almutairi-sara"),
        ("Sara2", "Almutairi9", "almutairi-sara"),
    ],
)
def test_normalise_base(first, last, expected):
    assert directory.normalise_base(first, last) == expected


def test_normalise_base_with_nothing_usable():
    with pytest.raises(PermanentToolError):
        directory.normalise_base("...", "!!!")


# Collision ladder


async def test_first_address_has_no_suffix(db):
    address = await directory.find_free_address(db, "almutairi-sara", DOMAIN)
    assert address == "almutairi-sara@gaia.sa"


async def test_collision_ladder(db):
    await make_account(db, "almutairi-sara@gaia.sa", "k-1")
    assert await directory.find_free_address(db, "almutairi-sara", DOMAIN) == (
        "almutairi-sara-2@gaia.sa"
    )

    await make_account(db, "almutairi-sara-2@gaia.sa", "k-2")
    assert await directory.find_free_address(db, "almutairi-sara", DOMAIN) == (
        "almutairi-sara-3@gaia.sa"
    )


async def test_collision_cap_exceeded_is_permanent(db):
    await make_account(db, "almutairi-sara@gaia.sa", "k-1")
    await make_account(db, "almutairi-sara-2@gaia.sa", "k-2")
    with pytest.raises(PermanentToolError):
        await directory.find_free_address(db, "almutairi-sara", DOMAIN, cap=2)


# Accounts and groups


async def test_same_address_from_another_run_is_retryable(db):
    await make_account(db, "almutairi-sara@gaia.sa", "run-1:2:create_account")
    with pytest.raises(AddressTakenError):
        await make_account(db, "almutairi-sara@gaia.sa", "run-2:2:create_account")
    assert await effect_count(db) == 1


async def test_create_account_is_idempotent(db):
    first = await make_account(db, "almutairi-sara@gaia.sa", "run-1:2:create_account")
    second = await make_account(db, "almutairi-sara@gaia.sa", "run-1:2:create_account")
    assert first == second
    assert await effect_count(db) == 1


async def test_setup_link_is_not_an_effect(db):
    account = await make_account(db, "almutairi-sara@gaia.sa")
    link = await directory.create_setup_link(db, account["account_id"])
    assert link["setup_link"].startswith("https://")
    assert await effect_count(db) == 1


async def test_add_to_groups_waits_for_the_account(db):
    with pytest.raises(TransientToolError):
        await directory.add_to_groups(db, "acct-missing", ["eng"], "run-1:4:add_to_groups")


async def test_add_to_groups(db):
    account = await make_account(db, "almutairi-sara@gaia.sa")
    key = "run-1:4:add_to_groups"

    first = await directory.add_to_groups(db, account["account_id"], ["eng", "vpn"], key)
    second = await directory.add_to_groups(db, account["account_id"], ["eng", "vpn"], key)
    assert first == second
    assert first["groups"] == ["eng", "vpn"]
    assert await effect_count(db) == 2
