from app.db import Database
from app.redaction import redact


async def append(
    db: Database, run_id: str, step_idx: int | None, event: str, detail: dict | None = None
) -> None:
    await db.insert_trace(run_id, step_idx, event, redact(detail) if detail is not None else None)
