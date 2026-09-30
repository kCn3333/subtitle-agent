import shutil
import subprocess
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
    return Settings(data_root=tmp_path / "data", media_roots=[media_root], ffprobe_timeout_seconds=1,
                    ffmpeg_timeout_seconds=1, subtitle_agent_app_mode="ADVANCED")


@pytest.fixture
def client(settings: Settings, monkeypatch):
    # TestClient starts lifespan in a helper thread; keep process probing in its
    # dedicated test to avoid platform-specific subprocess behavior in threads.
    monkeypatch.setattr("app.main.probe_tools", lambda: ToolInfo("ffmpeg version test", "ffprobe version test",
                                                                 "mkvextract version test"))
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
def mov_text_media(settings, require_tools):
    source = settings.data_root / "fixture.srt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("1\n00:00:00,500 --> 00:00:01,500\nHello world.\n", encoding="utf-8")
    media = settings.media_roots[0] / "Example Movie.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=1:duration=2",
        "-i", str(source), "-map", "0:v", "-map", "1:s", "-c:v", "mpeg4", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=eng", "-y", str(media),
    ], check=True, capture_output=True, timeout=10)
    return media


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def pgs_ocr_case():
    first, last, count = 107_483, 7_442_728, 2268

    def stamp(value):
        return f'{value // 3600000:02d}:{value // 60000 % 60:02d}:{value // 1000 % 60:02d},{value % 1000:03d}'

    cues, events = [], []
    for index in range(count):
        start = first + index * (last - first - 1000) // (count - 1)
        end = start + 1000
        text = "-| think |'ve seen it." if index == 0 else 'English subtitle sentence.'
        cues.append(f'{index + 1}\n{stamp(start)} --> {stamp(end)}\n{text}\n')
        events.extend([{'start_ms': start}, {'start_ms': end}])
    return {'content': '\n'.join(cues).encode(), 'events': events,
            'timeline': {'codec': 'hdmv_pgs_subtitle', 'cueCount': len(events), 'firstMs': first, 'lastMs': last}}
