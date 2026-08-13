from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
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
    alignment_min_scale: float = 0.94
    alignment_max_scale: float = 1.06
    alignment_max_segments: int = 3
    alignment_min_points_per_segment: int = 4
    alignment_end_tolerance_ms: int = 1000
    openai_api_key: SecretStr | None = Field(default=None, repr=False)
    openai_api_key_file: Path | None = None
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: str = "low"
    openai_timeout_seconds: float = 90
    openai_max_retries: int = 3
    openai_max_requests_per_job: int = 24
    openai_max_input_tokens_per_job: int = 120000
    openai_max_output_tokens_per_job: int = 12000
    openai_max_concurrent_requests: int = 2
    openai_semantic_alignment_enabled: bool = False
    openai_semantic_window_size: int = 18
    openai_semantic_window_overlap: int = 4
    openai_min_confidence: float = 0.72

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

    @field_validator("openai_timeout_seconds")
    @classmethod
    def positive_openai_timeout(cls, value: float) -> float:
        if value <= 0: raise ValueError("OPENAI_TIMEOUT_SECONDS must be greater than zero")
        return value

    @field_validator("openai_max_retries", "openai_max_requests_per_job", "openai_max_input_tokens_per_job",
                     "openai_max_output_tokens_per_job", "openai_max_concurrent_requests", "openai_semantic_window_size")
    @classmethod
    def positive_openai_limit(cls, value: int) -> int:
        if value < 1: raise ValueError("OpenAI limits must be at least 1")
        return value

    @field_validator("openai_reasoning_effort")
    @classmethod
    def valid_reasoning_effort(cls, value: str) -> str:
        if value not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError("Unsupported OPENAI_REASONING_EFFORT")
        return value

    @field_validator("openai_min_confidence")
    @classmethod
    def confidence_range(cls, value: float) -> float:
        if not 0 <= value <= 1: raise ValueError("OPENAI_MIN_CONFIDENCE must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def load_api_key_file(self):
        if self.openai_api_key_file is not None:
            try:
                value = self.openai_api_key_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError("OPENAI_API_KEY_FILE is not readable") from exc
            if not value: raise ValueError("OPENAI_API_KEY_FILE is empty")
            self.openai_api_key = SecretStr(value)
        return self

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
