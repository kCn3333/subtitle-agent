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
    subtitle_agent_app_mode: str = "WORKPACK"
    workpack_reference_score_margin: int = 10
    workpack_max_reference_alternatives: int = 2
    workpack_include_reference_alternatives: bool = True
    workpack_max_polish_candidates: int = 10
    workpack_max_archive_bytes: int = 104857600
    workpack_max_files: int = 100
    workpack_retention_hours: int = 72
    workpack_cleanup_interval_hours: int = 6
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
    subtitle_agent_publish_enabled: bool = False
    subtitle_agent_publish_mode: str = "PREVIEW_ONLY"
    subtitle_agent_publish_mappings_json: dict[Path, Path] = Field(default_factory=lambda: {
        Path("/media/movies"): Path("/publish/movies"), Path("/media/shows"): Path("/publish/shows")})
    subtitle_agent_auto_publish_min_quality: str = "HIGH"
    subtitle_agent_auto_publish_require_semantic: bool = True
    subtitle_agent_publish_max_version: int = 999
    subtitle_agent_publish_file_mode: int = 0o644

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

    @field_validator("subtitle_agent_app_mode")
    @classmethod
    def app_mode(cls, value: str) -> str:
        value = value.upper()
        if value not in {"WORKPACK", "ADVANCED"}:
            raise ValueError("SUBTITLE_AGENT_APP_MODE must be WORKPACK or ADVANCED")
        return value

    @field_validator("workpack_reference_score_margin", "workpack_max_reference_alternatives",
                     "workpack_max_polish_candidates", "workpack_max_archive_bytes", "workpack_max_files",
                     "workpack_retention_hours", "workpack_cleanup_interval_hours")
    @classmethod
    def positive_workpack_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Workpack limits must be at least 1")
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

    @field_validator("subtitle_agent_publish_mode")
    @classmethod
    def publish_mode(cls, value: str) -> str:
        if value not in {"PREVIEW_ONLY", "MANUAL", "AUTO_HIGH"}:
            raise ValueError("SUBTITLE_AGENT_PUBLISH_MODE is invalid")
        return value

    @field_validator("subtitle_agent_auto_publish_min_quality")
    @classmethod
    def publish_quality(cls, value: str) -> str:
        if value not in {"HIGH", "MEDIUM"}:
            raise ValueError("SUBTITLE_AGENT_AUTO_PUBLISH_MIN_QUALITY must be HIGH or MEDIUM")
        return value

    @field_validator("subtitle_agent_publish_max_version")
    @classmethod
    def publish_version(cls, value: int) -> int:
        if not 1 <= value <= 999:
            raise ValueError("SUBTITLE_AGENT_PUBLISH_MAX_VERSION must be between 1 and 999")
        return value

    @field_validator("subtitle_agent_publish_file_mode", mode="before")
    @classmethod
    def publish_file_mode(cls, value: object) -> int:
        parsed = int(value, 8) if isinstance(value, str) else int(value)
        if parsed < 0 or parsed > 0o777:
            raise ValueError("SUBTITLE_AGENT_PUBLISH_FILE_MODE must be an octal mode up to 0777")
        return parsed

    @field_validator("subtitle_agent_publish_mappings_json")
    @classmethod
    def publish_mappings(cls, value: dict[Path, Path]) -> dict[Path, Path]:
        if not value or any(not source.is_absolute() or not target.is_absolute() for source, target in value.items()):
            raise ValueError("Publish mappings must contain absolute source and target paths")
        return value

    @model_validator(mode="after")
    def load_api_key_file(self):
        if self.subtitle_agent_app_mode == "WORKPACK":
            # WORKPACK is deliberately independent from credentials, even if
            # stale OpenAI variables remain in the container environment.
            self.openai_api_key = None
            return self
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
