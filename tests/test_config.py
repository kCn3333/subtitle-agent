from pathlib import Path

import pytest

from app.core.config import Settings


def test_media_roots_accept_comma_separated_environment_value(monkeypatch):
    monkeypatch.setenv("MEDIA_ROOTS", "/media/movies,/media/shows")

    settings = Settings()

    assert settings.media_roots == [Path("/media/movies"), Path("/media/shows")]


def test_media_roots_ignore_whitespace_and_empty_items(monkeypatch):
    monkeypatch.setenv("MEDIA_ROOTS", " /media/movies, ,/media/shows ")

    settings = Settings()

    assert settings.media_roots == [Path("/media/movies"), Path("/media/shows")]


def test_prefixed_media_roots_accept_colon_separator(monkeypatch):
    monkeypatch.setenv("SUBTITLE_AGENT_MEDIA_ROOTS", "/media/movies:/media/shows")

    settings = Settings()

    assert settings.media_roots == [Path("/media/movies"), Path("/media/shows")]


def test_default_ffmpeg_timeout_is_ten_minutes(monkeypatch):
    monkeypatch.delenv("FFMPEG_TIMEOUT_SECONDS", raising=False)
    assert Settings().ffmpeg_timeout_seconds == 600


def test_ocr_worker_is_optional_and_requires_internal_http():
    assert Settings(_env_file=None).ocr_worker_url is None
    assert Settings(_env_file=None, ocr_worker_url="http://subtitle-ocr-worker:8090/").ocr_worker_url == (
        "http://subtitle-ocr-worker:8090"
    )
    with pytest.raises(ValueError):
        Settings(_env_file=None, ocr_worker_url="https://external.example")


def test_include_graphic_reference_environment(monkeypatch):
    monkeypatch.delenv('INCLUDE_GRAPHIC_REFERENCE', raising=False)
    assert Settings(_env_file=None).include_graphic_reference is False
    monkeypatch.setenv('INCLUDE_GRAPHIC_REFERENCE', 'true')
    assert Settings(_env_file=None).include_graphic_reference is True


def test_removed_advanced_environment_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv('SUBTITLE_AGENT_APP_MODE', 'ADVANCED')
    monkeypatch.setenv('OPENAI_API_KEY_FILE', str(tmp_path/'missing-secret'))
    monkeypatch.setenv('SUBTITLE_AGENT_PUBLISH_ENABLED', 'true')
    Settings(_env_file=None)
    assert 'subtitle_agent_app_mode' not in Settings.model_fields
    assert not any(name.startswith(('openai_', 'subtitle_agent_publish_', 'subtitle_agent_auto_publish_', 'alignment_'))
                   for name in Settings.model_fields)
