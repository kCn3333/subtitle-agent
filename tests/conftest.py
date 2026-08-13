import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.system_probe import ToolInfo


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    media_root = tmp_path / "media"
    media_root.mkdir()
    return Settings(data_root=tmp_path / "data", media_roots=[media_root], ffprobe_timeout_seconds=1, ffmpeg_timeout_seconds=1)


@pytest.fixture
def client(settings: Settings, monkeypatch):
    # TestClient starts lifespan in a helper thread; keep process probing in its
    # dedicated test to avoid platform-specific subprocess behavior in threads.
    monkeypatch.setattr("app.main.probe_tools", lambda: ToolInfo("ffmpeg version test", "ffprobe version test"))
    async def fake_probe(path, timeout):
        return {"path": str(path), "name": path.name, "sizeBytes": path.stat().st_size, "container": "matroska",
                "durationSeconds": 100.0, "bitrate": 1000, "width": 1920, "height": 1080,
                "rFrameRate": "24/1", "avgFrameRate": "24/1", "videoCodec": "h264",
                "audioTracks": [], "embeddedSubtitles": []}
    async def fake_extract(*args, **kwargs): return None
    monkeypatch.setattr("app.services.job_manager.probe_media", fake_probe)
    monkeypatch.setattr("app.services.job_manager.extract_reference", fake_extract)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def media_file(settings: Settings) -> Path:
    path = settings.media_roots[0] / "Example Movie.mkv"
    path.write_bytes(b"not-real-media")
    return path


@pytest.fixture
def require_tools():
    assert shutil.which("ffmpeg")
    assert shutil.which("ffprobe")


@pytest.fixture
def anyio_backend():
    return "asyncio"
