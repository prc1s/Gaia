import re
import unicodedata
import uuid

from app.errors import AccountNotVisibleError, AddressTakenError, PermanentToolError

MAX_SUFFIX_DEFAULT = 50


def normalise_base(first_name: str, last_name: str) -> str:
    """Sara Almutairi -> almutairi-sara"""
    base = f"{last_name}-{first_name}"
    decomposed = unicodedata.normalize("NFKD", base)

    stripped = ""
    for ch in decomposed:
        if not unicodedata.combining(ch):
            stripped += ch

    stripped = stripped.lower()
    stripped = re.sub(r"[^a-z-]", "", stripped)
    stripped = re.sub(r"-+", "-", stripped).strip("-")

    if not stripped:
        raise PermanentToolError(f"cannot derive an address from {first_name!r} {last_name!r}")
    return stripped


async def taken_addresses(db) -> set[str]:
    rows = await db.load_effects_of_kind("account")

    taken = set()
    for payload in rows:
        taken.add(payload["address"])
    return taken


async def find_free_address(db, base: str, domain: str, cap: int = MAX_SUFFIX_DEFAULT) -> str:
    """Collision ladder: base, base-2, base-3, ... capped."""
    taken = await taken_addresses(db)

    for suffix in range(1, cap + 1):
        local = base if suffix == 1 else f"{base}-{suffix}"
        address = f"{local}@{domain}"
        if address not in taken:
            return address

    raise PermanentToolError(f"address collision cap exceeded for {base}")


async def create_account(db, address: str, idempotency_key: str) -> dict:
    """The one unique-address check; a loser gets AddressTakenError and re-scans."""
    existing = await db.load_effect(idempotency_key)
    if existing is not None:
        return existing

    if address in await taken_addresses(db):
        raise AddressTakenError(f"address already allocated: {address}")

    payload = {"address": address, "account_id": f"acct-{uuid.uuid4().hex[:12]}"}
    effect, _ = await db.record_effect(idempotency_key, "account", payload)
    return effect


async def create_setup_link(db, account_id: str) -> dict:
    """One-time link. No password is ever generated or stored."""
    return {
        "setup_link": f"https://directory.gaia.sa/setup/{uuid.uuid4().hex}",
        "link_expires_at": "2026-09-23T00:00:00+00:00",
    }


async def add_to_groups(db, account_id: str, groups: list[str], idempotency_key: str) -> dict:
    """Raises AccountNotVisibleError while the account is still propagating."""
    accounts = await db.load_effects_of_kind("account")

    known = set()
    for account in accounts:
        known.add(account["account_id"])

    if account_id not in known:
        raise AccountNotVisibleError(f"account not visible yet: {account_id}")

    payload = {"account_id": account_id, "groups": sorted(groups)}
    effect, _ = await db.record_effect(idempotency_key, "groups", payload)
    return effect
