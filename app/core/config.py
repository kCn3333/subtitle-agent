from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Subtitle Agent"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    log_level: str = "INFO"
    data_root: Path = Path("/data")
    media_roots: list[Path] = [Path("/media/movies"), Path("/media/shows")]
    max_concurrent_jobs: int = 1
    demo_step_delay: float = 0.25

    @field_validator("media_roots", mode="before")
    @classmethod
    def split_roots(cls, value: object) -> object:
        if isinstance(value, str):
            return [Path(item.strip()) for item in value.split(",") if item.strip()]
        return value

    @field_validator("max_concurrent_jobs")
    @classmethod
    def positive_jobs(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MAX_CONCURRENT_JOBS must be at least 1")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
