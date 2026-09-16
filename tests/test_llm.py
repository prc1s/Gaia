import json

import pytest

from app.config import Settings
from app.errors import TransientToolError
from app.groups import ALLOWED_GROUPS, GROUP_MAP, groups_for_role
from app.llm import CANNED, FakeLLM, build_llm, propose_equipment, resolve_groups


def fake_with(task: str, answer: str) -> FakeLLM:
    answers = dict(CANNED)
    answers[task] = answer
    return FakeLLM(answers=answers)


# Group map


def test_group_map_only_uses_allowed_groups():
    for role, groups in GROUP_MAP.items():
        for group in groups:
            assert group in ALLOWED_GROUPS, f"{role} maps to unknown group {group}"


def test_groups_for_role():
    assert groups_for_role("Backend Engineer") == ["all-staff", "engineering", "vpn", "github"]
    assert groups_for_role("Ceramics Technician") is None


def test_groups_for_role_returns_a_copy():
    groups = groups_for_role("Backend Engineer")
    groups.append("finance")
    assert "finance" not in GROUP_MAP["Backend Engineer"]


# Groups from the LLM


async def test_resolve_groups_parses_canned_output():
    llm = FakeLLM()
    assert await resolve_groups(llm, "Ceramics Technician", "Operations") == [
        "all-staff",
        "engineering",
        "vpn",
    ]


async def test_resolve_groups_prompt_carries_the_allowlist():
    llm = FakeLLM()
    await resolve_groups(llm, "Ceramics Technician", "Operations")
    task, prompt = llm.calls[0]
    assert task == "resolve_groups"
    assert "workshop" in prompt and "Ceramics Technician" in prompt


async def test_groups_outside_the_allowlist_are_dropped():
    llm = fake_with("resolve_groups", '{"groups": ["engineering", "root-everything", 7]}')
    assert await resolve_groups(llm, "x", "y") == ["engineering"]


async def test_duplicate_groups_are_dropped():
    llm = fake_with("resolve_groups", '{"groups": ["vpn", "vpn"]}')
    assert await resolve_groups(llm, "x", "y") == ["vpn"]


async def test_no_valid_groups_is_retryable():
    llm = fake_with("resolve_groups", '{"groups": ["root-everything"]}')
    with pytest.raises(TransientToolError):
        await resolve_groups(llm, "x", "y")


async def test_missing_groups_key_is_retryable():
    llm = fake_with("resolve_groups", '{"roles": ["engineering"]}')
    with pytest.raises(TransientToolError):
        await resolve_groups(llm, "x", "y")


async def test_garbage_groups_output_is_retryable():
    with pytest.raises(TransientToolError):
        await resolve_groups(FakeLLM(mode="garbage"), "x", "y")


# Equipment


async def test_propose_equipment_parses_canned_output():
    proposal = await propose_equipment(FakeLLM(), "Backend Engineer", "Engineering")
    assert proposal.laptop_model == "MacBook Pro 16 M4"
    assert proposal.ram_gb == 48
    assert proposal.estimated_cost_sar == 8400


async def test_garbage_equipment_output_is_retryable():
    with pytest.raises(TransientToolError):
        await propose_equipment(FakeLLM(mode="garbage"), "x", "y")


async def test_json_array_is_retryable():
    llm = fake_with("propose_equipment", "[1, 2, 3]")
    with pytest.raises(TransientToolError):
        await propose_equipment(llm, "x", "y")


@pytest.mark.parametrize(
    "answer",
    [
        json.dumps({"laptop_model": "X", "ram_gb": 16}),
        json.dumps(
            {"laptop_model": "X", "ram_gb": "lots", "estimated_cost_sar": 10, "justification": "j"}
        ),
        json.dumps(
            {"laptop_model": "X", "ram_gb": 16, "estimated_cost_sar": -5, "justification": "j"}
        ),
        json.dumps(
            {
                "laptop_model": "X",
                "ram_gb": 16,
                "estimated_cost_sar": 10,
                "justification": "j",
                "extra": True,
            }
        ),
    ],
)
async def test_bad_equipment_schema_is_retryable(answer):
    llm = fake_with("propose_equipment", answer)
    with pytest.raises(TransientToolError):
        await propose_equipment(llm, "x", "y")


# Client choice


def test_build_llm_without_a_key_is_fake():
    assert isinstance(build_llm(Settings(anthropic_api_key=None)), FakeLLM)
