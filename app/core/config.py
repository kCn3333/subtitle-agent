from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    app_name: str = "Subtitle Agent"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    log_level: str = "INFO"
    data_root: Path = Path("/data")
    # NoDecode keeps pydantic-settings from treating this environment value as
    # JSON before our documented comma-separated parser can process it.
    media_roots: Annotated[list[Path], NoDecode] = Field(
        default=[Path("/media/movies"), Path("/media/shows")],
        validation_alias=AliasChoices("SUBTITLE_AGENT_MEDIA_ROOTS", "MEDIA_ROOTS"),
    )
    max_concurrent_jobs: int = 1
    ffprobe_timeout_seconds: float = 30
    ffmpeg_timeout_seconds: float = 600

    @field_validator("media_roots", mode="before")
    @classmethod
    def split_roots(cls, value: object) -> object:
        if isinstance(value, str):
            separator = ":" if ":" in value else ","
            return [Path(item.strip()) for item in value.split(separator) if item.strip()]
        return value

    @field_validator("ffprobe_timeout_seconds", "ffmpeg_timeout_seconds")
    @classmethod
    def positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Timeout must be greater than zero")
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
