from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://synckit:synckit@localhost:5432/po_ue"
    redis_url: str = "redis://localhost:6379/0"
    broker: str = "redis"  # "redis" or "stub"
    redis_namespace: str = "ue"
    max_batch_size: int = 1000
    rollup_chunk_size: int = 20_000
    # Events older than this are rejected: the rollups are kept for this long.
    max_event_age_days: int = 90
    max_event_future_skew_seconds: int = 300
    margin_alert_window_days: int = 30
    webhook_timeout_seconds: float = 10.0
    webhook_max_retries: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
