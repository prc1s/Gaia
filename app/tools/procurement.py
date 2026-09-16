import uuid


async def create_order(db, item: str, cost_sar: int, for_address: str, idempotency_key: str) -> dict:
    payload = {
        "order_id": f"ord-{uuid.uuid4().hex[:12]}",
        "item": item,
        "cost_sar": cost_sar,
        "for_address": for_address,
    }
    effect, _ = await db.record_effect(idempotency_key, "order", payload)
    return effect
