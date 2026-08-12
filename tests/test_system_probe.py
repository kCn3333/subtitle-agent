from app.services.system_probe import probe_tools


def test_detects_ffmpeg_and_ffprobe(require_tools):
    tools = probe_tools()
    assert tools.ffmpeg.startswith("ffmpeg version")
    assert tools.ffprobe.startswith("ffprobe version")
