from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="GAIA onboarding orchestrator")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "llm_configured": get_settings().anthropic_api_key is not None}
