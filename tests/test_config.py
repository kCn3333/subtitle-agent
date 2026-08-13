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
