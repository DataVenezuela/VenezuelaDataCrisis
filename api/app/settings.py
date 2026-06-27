from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VZLA_DEDUP_API_")

    service_name: str = "vzla-dedup-api"
    version: str = "0.1.0"


settings = Settings()
