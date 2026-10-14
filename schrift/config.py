from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from SCHRIFT_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="SCHRIFT_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    base_url: str = "http://localhost:8000"
    secret_key: str = "change-me"
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
