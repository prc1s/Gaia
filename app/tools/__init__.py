from app.errors import GuardrailViolation, TransientToolError
from app.tools import directory, hr, mail, procurement

TOOLS = {
    "hr.get_employee": hr.get_employee,
    "directory.create_account": directory.create_account,
    "directory.create_setup_link": directory.create_setup_link,
    "directory.add_to_groups": directory.add_to_groups,
    "mail.send": mail.send,
    "procurement.create_order": procurement.create_order,
}


class FaultInjector:
    """Makes named tools fail a set number of times, then stop failing."""

    def __init__(self, fail_n_times: dict[str, int] | None = None) -> None:
        self.remaining = dict(fail_n_times or {})

    def check(self, tool: str) -> None:
        left = self.remaining.get(tool, 0)
        if left > 0:
            self.remaining[tool] = left - 1
            raise TransientToolError(f"injected failure: {tool}")


class ToolBox:
    def __init__(self, db, faults: FaultInjector | None = None) -> None:
        self.db = db
        self.faults = faults or FaultInjector()

    async def dispatch(self, tool: str, allowed: frozenset[str], **kwargs):
        if tool not in allowed:
            raise GuardrailViolation(f"tool not allowed for this step: {tool}")

        fn = TOOLS.get(tool)
        if fn is None:
            raise GuardrailViolation(f"unknown tool: {tool}")

        self.faults.check(tool)
        return await fn(self.db, **kwargs)
