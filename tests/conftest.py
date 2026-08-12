import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.system_probe import ToolInfo


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_root=tmp_path / "data", media_roots=[tmp_path / "media", Path("/media/shows")], demo_step_delay=0.01)


@pytest.fixture
def client(settings: Settings, monkeypatch):
    # TestClient starts lifespan in a helper thread; keep process probing in its
    # dedicated test to avoid platform-specific subprocess behavior in threads.
    monkeypatch.setattr("app.main.probe_tools", lambda: ToolInfo("ffmpeg version test", "ffprobe version test"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def require_tools():
    assert shutil.which("ffmpeg")
    assert shutil.which("ffprobe")
