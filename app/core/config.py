from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator
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
    auth_username: str | None = Field(default=None, max_length=200)
    auth_password: SecretStr | None = Field(default=None, repr=False)
    auth_public_url: str = ""
    auth_cookie_secure: bool = True
    auth_session_seconds: int = Field(default=28800, ge=300, le=86400)
    auth_lockout_seconds: int = Field(default=3600, ge=60, le=604800)
    auth_trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # NoDecode keeps pydantic-settings from treating this environment value as
    # JSON before our documented comma-separated parser can process it.
    media_roots: Annotated[list[Path], NoDecode] = Field(
        default=[Path("/media/movies"), Path("/media/shows")],
        validation_alias=AliasChoices("SUBTITLE_AGENT_MEDIA_ROOTS", "MEDIA_ROOTS"),
    )
    max_concurrent_jobs: int = 1
    max_queued_jobs: int = Field(default=5, ge=1, le=100)
    max_queued_ai_operations: int = Field(default=5, ge=1, le=100)
    workpack_reference_score_margin: int = 10
    workpack_max_reference_alternatives: int = 2
    workpack_include_reference_alternatives: bool = True
    workpack_max_polish_candidates: int = 10
    workpack_max_archive_bytes: int = 104857600
    workpack_max_files: int = 100
    directory_max_items: int = Field(default=500, ge=1, le=10000)
    directory_max_archive_bytes: int = Field(default=1073741824, ge=4096)
    directory_max_files: int = Field(default=10000, ge=3)
    ai_max_saved_models: int = Field(default=10, ge=1, le=100)
    archive_max_titles: int = Field(default=30, ge=1, le=10000)
    workpack_cleanup_interval_hours: int = 6
    ffprobe_timeout_seconds: float = 30
    ffmpeg_timeout_seconds: float = 600
    include_graphic_reference: bool = False
    ocr_worker_url: str | None = None
    ocr_timeout_seconds: float = 900
    ocr_max_output_bytes: int = 20 * 1024 * 1024

    @field_validator("auth_trusted_proxies", mode="before")
    @classmethod
    def trusted_proxy_networks(cls, value):
        from ipaddress import ip_network
        items = value.split(",") if isinstance(value, str) else value
        networks = [ip_network(item.strip()) for item in items if item.strip()]
        if any(network.prefixlen == 0 for network in networks):
            raise ValueError("AUTH_TRUSTED_PROXIES must not trust all addresses")
        return [str(network) for network in networks]

    @field_validator("auth_public_url")
    @classmethod
    def public_origin(cls, value):
        from urllib.parse import urlsplit
        value = value.rstrip("/")
        parsed = urlsplit(value)
        if value and (parsed.scheme not in {"http", "https"} or not parsed.hostname
                      or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("AUTH_PUBLIC_URL must be an HTTP(S) origin without a path")
        if value:
            parsed.port  # Reject malformed ports.
        return value

    def require_auth_config(self):
        if not self.auth_username or not self.auth_username.strip() or not self.auth_password:
            raise ValueError("AUTH_USERNAME and AUTH_PASSWORD are required")
        if not 16 <= len(self.auth_password.get_secret_value()) <= 1024:
            raise ValueError("AUTH_PASSWORD must contain between 16 and 1024 characters")
        if not self.auth_public_url:
            raise ValueError("AUTH_PUBLIC_URL is required")
        if self.auth_cookie_secure and not self.auth_public_url.startswith("https://"):
            raise ValueError("AUTH_PUBLIC_URL must use HTTPS when AUTH_COOKIE_SECURE=true")

    @field_validator("media_roots", mode="before")
    @classmethod
    def split_roots(cls, value: object) -> object:
        if isinstance(value, str):
            separator = ":" if ":" in value else ","
            return [Path(item.strip()) for item in value.split(separator) if item.strip()]
        return value

    @field_validator("ffprobe_timeout_seconds", "ffmpeg_timeout_seconds", "ocr_timeout_seconds")
    @classmethod
    def positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Timeout must be greater than zero")
        return value

    @field_validator("ocr_worker_url")
    @classmethod
    def internal_ocr_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.rstrip("/")
        if not normalized.startswith("http://"):
            raise ValueError("OCR_WORKER_URL must use internal plain HTTP")
        return normalized

    @field_validator("max_concurrent_jobs")
    @classmethod
    def positive_jobs(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MAX_CONCURRENT_JOBS must be at least 1")
        return value

    @field_validator("workpack_reference_score_margin", "workpack_max_reference_alternatives",
                     "workpack_max_polish_candidates", "workpack_max_archive_bytes", "workpack_max_files",
                     "workpack_cleanup_interval_hours", "ocr_max_output_bytes")
    @classmethod
    def positive_workpack_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Workpack limits must be at least 1")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
