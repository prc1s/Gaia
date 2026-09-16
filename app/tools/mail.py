async def send(db, to: str, subject: str, body: str, idempotency_key: str) -> dict:
    payload = {"to": to, "subject": subject, "body": body}
    effect, _ = await db.record_effect(idempotency_key, "email", payload)
    return effect
