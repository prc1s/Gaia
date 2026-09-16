from app.groups import GROUP_MAP, learn_role, resolve_from_mapping


async def test_static_map_wins(db):
    assert await resolve_from_mapping(db, "Backend Engineer") == GROUP_MAP["Backend Engineer"]


async def test_unmapped_role_has_no_mapping(db):
    assert await resolve_from_mapping(db, "Ceramics Technician") is None


async def test_approved_role_is_remembered(db):
    await learn_role(db, "Ceramics Technician", ["all-staff", "workshop"])
    assert await resolve_from_mapping(db, "Ceramics Technician") == ["all-staff", "workshop"]


async def test_learning_the_same_role_twice_keeps_the_first_decision(db):
    await learn_role(db, "Ceramics Technician", ["all-staff", "workshop"])
    await learn_role(db, "Ceramics Technician", ["all-staff", "finance"])
    assert await resolve_from_mapping(db, "Ceramics Technician") == ["all-staff", "workshop"]


async def test_learned_roles_are_listed(db):
    await learn_role(db, "Ceramics Technician", ["workshop"])
    await learn_role(db, "Welder", ["operations"])
    assert await db.load_all_role_groups() == {
        "Ceramics Technician": ["workshop"],
        "Welder": ["operations"],
    }


async def test_learning_does_not_touch_the_static_map(db):
    await learn_role(db, "Backend Engineer", ["finance"])
    assert await resolve_from_mapping(db, "Backend Engineer") == GROUP_MAP["Backend Engineer"]
