"""Persist only settings whose consumers can use new values without a restart."""
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings


class AppSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    ffprobe_timeout_seconds: float = Field(gt=0, le=86400)
    ffmpeg_timeout_seconds: float = Field(gt=0, le=86400)
    workpack_reference_score_margin: int = Field(ge=1, le=10000)
    workpack_max_polish_candidates: int = Field(ge=1, le=1000)
    workpack_max_archive_bytes: int = Field(ge=4096, le=100 * 1024**3)
    workpack_max_files: int = Field(ge=3, le=100000)
    directory_max_items: int = Field(ge=1, le=10000)
    directory_max_archive_bytes: int = Field(ge=4096, le=100 * 1024**3)
    directory_max_files: int = Field(ge=3, le=100000)
    include_graphic_reference: bool
    ocr_worker_url: str | None = Field(max_length=2048)
    ocr_timeout_seconds: float = Field(gt=0, le=86400)
    ocr_max_output_bytes: int = Field(ge=1, le=100 * 1024**2)

    @field_validator('ocr_worker_url')
    @classmethod
    def internal_ocr_url(cls, value):
        value = Settings.internal_ocr_url(value)
        if value:
            parsed = urlsplit(value)
            if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Adres OCR musi być adresem HTTP bez danych logowania, query i fragmentu')
            parsed.port  # Reject invalid port numbers before saving.
        return value


class AppSettingsStore:
    def __init__(self, path: Path, settings: Settings):
        self.path, self.settings = path, settings
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS app_settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            row = db.execute('SELECT data FROM app_settings WHERE id=1').fetchone()
        if row:
            # Environment provides defaults for fields introduced by future upgrades.
            saved = AppSettings.model_validate({**self.get(), **json.loads(row[0])})
            self.apply(saved)

    def get(self) -> dict:
        return {name: getattr(self.settings, name) for name in AppSettings.model_fields}

    def apply(self, value: AppSettings) -> None:
        for name, setting in value.model_dump().items():
            setattr(self.settings, name, setting)

    def save(self, value: AppSettings) -> dict:
        # Publish in memory only after the durable transaction succeeds.
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO app_settings VALUES (1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (value.model_dump_json(),))
        self.apply(value)
        return self.get()
