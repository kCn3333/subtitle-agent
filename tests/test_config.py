from pathlib import Path

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


def test_missing_openai_key_does_not_break_settings(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)
    settings = Settings()
    assert settings.openai_configured is False
    assert settings.openai_semantic_alignment_enabled is False


def test_openai_key_file_has_priority_and_is_masked(tmp_path, monkeypatch):
    secret = tmp_path / "key"
    secret.write_text("file-placeholder-secret\n")
    settings = Settings(subtitle_agent_app_mode="ADVANCED", openai_api_key="environment-placeholder",
                        openai_api_key_file=secret)
    assert settings.openai_api_key.get_secret_value() == "file-placeholder-secret"
    assert "file-placeholder-secret" not in repr(settings)
