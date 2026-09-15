# GAIA onboarding orchestrator

Durable agent orchestration layer for employee onboarding. Work in progress.

## Setup

```sh
uv pip sync requirements.txt
cp .env.example .env
```

## Run

```sh
uv run uvicorn app.main:app
uv run pytest
```
