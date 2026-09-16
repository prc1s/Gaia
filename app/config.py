from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from GAIA_* env vars and .env."""

    model_config = SettingsConfigDict(env_prefix="GAIA_", env_file=".env", extra="ignore")

    db_path: str = "gaia.db"

    approval_threshold_sar: int = 5000

    max_step_attempts: int = 3
    retry_delay_s: float = 1.0  # fixed wait between retries; 0 in tests

    max_steps: int = 20
    max_tool_calls: int = 40

    email_domain: str = "gaia.sa"
    address_collision_cap: int = 50

    anthropic_api_key: SecretStr | None = None  # no key -> FakeLLM
    llm_model: str = "claude-opus-5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
