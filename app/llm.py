import json
from typing import Protocol

from pydantic import ValidationError

from app.config import Settings
from app.errors import TransientToolError
from app.groups import ALLOWED_GROUPS
from app.models import EquipmentProposal

GROUPS_PROMPT = (
    "Pick the access groups for a new hire.\n"
    "Job title: {role}\nDepartment: {department}\n"
    "Choose only from this list: {allowed}\n"
    'Reply with JSON: {{"groups": ["...", "..."]}}'
)

EQUIPMENT_PROMPT = (
    "Propose a laptop for a new hire.\n"
    "Job title: {role}\nDepartment: {department}\n"
    'Reply with JSON: {{"laptop_model": "...", "ram_gb": 32, '
    '"estimated_cost_sar": 8000, "justification": "..."}}'
)

CANNED = {
    "resolve_groups": '{"groups": ["all-staff", "engineering", "vpn"]}',
    "propose_equipment": json.dumps(
        {
            "laptop_model": "MacBook Pro 16 M4",
            "ram_gb": 48,
            "estimated_cost_sar": 8400,
            "justification": "Compiles and container workloads need the memory.",
        }
    ),
}


class LLMClient(Protocol):
    async def complete(self, task: str, prompt: str) -> str: ...


class FakeLLM:
    """Default in tests and with no API key. mode='garbage' returns unparseable text."""

    def __init__(self, mode: str = "canned", answers: dict[str, str] | None = None) -> None:
        self.mode = mode
        self.answers = dict(answers) if answers else dict(CANNED)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, task: str, prompt: str) -> str:
        self.calls.append((task, prompt))
        if self.mode == "garbage":
            return "Sure! Here is what I think you should do."
        return self.answers[task]


class AnthropicLLM:
    def __init__(self, api_key: str, model: str) -> None:
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def complete(self, task: str, prompt: str) -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system="Reply with JSON only. No prose, no code fences.",
            messages=[{"role": "user", "content": prompt}],
        )

        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text
        return text


def build_llm(settings: Settings) -> LLMClient:
    if settings.anthropic_api_key is None:
        return FakeLLM()
    return AnthropicLLM(settings.anthropic_api_key.get_secret_value(), settings.llm_model)


def _parse_json(raw: str) -> dict:
    """Malformed output is retryable: the next attempt may come back clean."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise TransientToolError("llm did not return json")

    if not isinstance(data, dict):
        raise TransientToolError("llm returned json but not an object")
    return data


async def resolve_groups(llm: LLMClient, role: str, department: str) -> list[str]:
    """Anything outside the allowlist is dropped; an empty result is retryable."""
    prompt = GROUPS_PROMPT.format(
        role=role, department=department, allowed=", ".join(sorted(ALLOWED_GROUPS))
    )
    data = _parse_json(await llm.complete("resolve_groups", prompt))

    proposed = data.get("groups")
    if not isinstance(proposed, list):
        raise TransientToolError("llm output has no groups list")

    kept = []
    for group in proposed:
        if isinstance(group, str) and group in ALLOWED_GROUPS and group not in kept:
            kept.append(group)

    if not kept:
        raise TransientToolError("llm proposed no valid groups")
    return kept


async def propose_equipment(llm: LLMClient, role: str, department: str) -> EquipmentProposal:
    prompt = EQUIPMENT_PROMPT.format(role=role, department=department)
    data = _parse_json(await llm.complete("propose_equipment", prompt))

    try:
        return EquipmentProposal(**data)
    except ValidationError as exc:
        raise TransientToolError(f"equipment proposal failed validation: {exc.error_count()} errors")
