from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.errors import AddressTakenError
from app.groups import resolve_from_mapping
from app.llm import propose_equipment, resolve_groups
from app.redaction import mask_email
from app.tools.directory import find_free_address, normalise_base


@dataclass
class StepResult:
    output: dict | None = None
    pause: str | None = None
    skipped: bool = False


@dataclass
class StepDef:
    idx: int
    name: str
    fn: Callable[..., Awaitable[StepResult]]
    allowed_tools: frozenset[str]


async def fetch_employee(ctx) -> StepResult:
    employee = await ctx.call("hr.get_employee", employee_id=ctx.run.employee_id)

    output = dict(employee)
    output["personal_email"] = mask_email(employee["personal_email"])
    return StepResult(output=output)


async def create_account(ctx) -> StepResult:
    """The address is decided once, in the database, before the account exists."""
    employee = ctx.step_output(1)
    invocation = await ctx.invocation("directory.create_account")

    if invocation and invocation["args"].get("address"):
        address = invocation["args"]["address"]
    else:
        base = normalise_base(employee["first_name"], employee["last_name"])
        address = await find_free_address(
            ctx.db, base, ctx.settings.email_domain, ctx.settings.address_collision_cap
        )

    try:
        account = await ctx.call_effect("directory.create_account", address=address)
    except AddressTakenError:
        # Someone else took it. Drop the intent so the retry re-scans.
        await ctx.forget_invocation("directory.create_account")
        raise

    return StepResult(output=account)


async def send_credentials(ctx) -> StepResult:
    """The setup link reaches the mail tool and nothing else."""
    employee = await ctx.call("hr.get_employee", employee_id=ctx.run.employee_id)
    account = ctx.step_output(2)

    link = await ctx.call("directory.create_setup_link", account_id=account["account_id"])
    await ctx.call_effect(
        "mail.send",
        record={"to": mask_email(employee["personal_email"])},
        to=employee["personal_email"],
        subject="Set up your GAIA account",
        body=f"Welcome. Set your password here: {link['setup_link']}",
    )

    return StepResult(output={"sent": True, "link_expires_at": link["link_expires_at"]})


async def resolve_groups_step(ctx) -> StepResult:
    employee = ctx.step_output(1)
    account = ctx.step_output(2)

    groups = await resolve_from_mapping(ctx.db, employee["role"])
    source = "mapping"
    if groups is None:
        # Phase 5 pauses here for approval before applying an LLM proposal.
        groups = await resolve_groups(ctx.llm, employee["role"], employee["department"])
        source = "llm"

    await ctx.call_effect(
        "directory.add_to_groups", account_id=account["account_id"], groups=groups
    )
    return StepResult(output={"groups": groups, "source": source})


async def propose_equipment_step(ctx) -> StepResult:
    employee = ctx.step_output(1)
    proposal = await propose_equipment(ctx.llm, employee["role"], employee["department"])
    return StepResult(output=proposal.model_dump())


async def equipment_gate(ctx) -> StepResult:
    # Phase 5 adds the threshold pause. For now the gate records that it ran.
    return StepResult(skipped=True)


async def order_laptop(ctx) -> StepResult:
    account = ctx.step_output(2)
    proposal = ctx.step_output(5)

    order = await ctx.call_effect(
        "procurement.create_order",
        item=proposal["laptop_model"],
        cost_sar=proposal["estimated_cost_sar"],
        for_address=account["address"],
    )
    return StepResult(output=order)


async def notify_manager(ctx) -> StepResult:
    employee = ctx.step_output(1)
    account = ctx.step_output(2)
    groups = ctx.step_output(4)
    order = ctx.step_output(7)

    body = (
        f"{employee['first_name']} {employee['last_name']} starts {employee['start_date']}.\n"
        f"Address: {account['address']}\n"
        f"Groups: {', '.join(groups['groups'])}\n"
        f"Laptop order: {order['order_id']}"
    )
    await ctx.call_effect(
        "mail.send",
        record={"to": mask_email(employee["manager_email"])},
        to=employee["manager_email"],
        subject="New hire provisioned",
        body=body,
    )
    return StepResult(output={"sent": True})


STEPS: list[StepDef] = [
    StepDef(1, "fetch_employee", fetch_employee, frozenset({"hr.get_employee"})),
    StepDef(2, "create_account", create_account, frozenset({"directory.create_account"})),
    StepDef(
        3,
        "send_credentials",
        send_credentials,
        frozenset({"hr.get_employee", "directory.create_setup_link", "mail.send"}),
    ),
    StepDef(4, "resolve_groups", resolve_groups_step, frozenset({"directory.add_to_groups"})),
    StepDef(5, "propose_equipment", propose_equipment_step, frozenset()),
    StepDef(6, "equipment_gate", equipment_gate, frozenset()),
    StepDef(7, "order_laptop", order_laptop, frozenset({"procurement.create_order"})),
    StepDef(8, "notify_manager", notify_manager, frozenset({"mail.send"})),
]

STEP_NAMES = [step.name for step in STEPS]
