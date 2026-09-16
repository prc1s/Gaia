ALLOWED_GROUPS = frozenset(
    {
        "all-staff",
        "engineering",
        "vpn",
        "github",
        "aws-readonly",
        "data-platform",
        "operations",
        "workshop",
        "finance",
        "people-ops",
    }
)

# Known titles resolve here. Anything else falls through to the LLM.
GROUP_MAP = {
    "Backend Engineer": ["all-staff", "engineering", "vpn", "github"],
    "Frontend Engineer": ["all-staff", "engineering", "vpn", "github"],
    "Data Scientist": ["all-staff", "engineering", "vpn", "data-platform"],
    "Data Engineer": ["all-staff", "engineering", "vpn", "data-platform", "github"],
    "Accountant": ["all-staff", "finance"],
    "Recruiter": ["all-staff", "people-ops"],
}


def groups_for_role(role: str) -> list[str] | None:
    groups = GROUP_MAP.get(role)
    return list(groups) if groups else None


async def resolve_from_mapping(db, role: str) -> list[str] | None:
    """Static map first, then titles approved on earlier runs. None means ask the LLM."""
    groups = groups_for_role(role)
    if groups:
        return groups
    return await db.load_role_groups(role)


async def learn_role(db, role: str, groups: list[str]) -> None:
    """Called after a human approves an LLM proposal, so the title never needs one again."""
    await db.save_role_groups(role, groups)
