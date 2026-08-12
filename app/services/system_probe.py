import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ToolInfo:
    ffmpeg: str
    ffprobe: str


def _version(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise RuntimeError(f"Wymagane narzędzie {binary} nie jest dostępne w PATH")
    result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10, check=True)
    return result.stdout.splitlines()[0]


def probe_tools() -> ToolInfo:
    return ToolInfo(ffmpeg=_version("ffmpeg"), ffprobe=_version("ffprobe"))


def prepare_data_root(data_root: Path) -> Path:
    data_root.mkdir(parents=True, exist_ok=True)
    if not data_root.is_dir():
        raise RuntimeError(f"DATA_ROOT nie jest katalogiem: {data_root}")
    marker = data_root / ".write-test"
    try:
        marker.touch(exist_ok=True)
        marker.unlink()
    except OSError as exc:
        raise RuntimeError(f"DATA_ROOT nie jest zapisywalny: {data_root}") from exc
    jobs = data_root / "jobs"
    jobs.mkdir(exist_ok=True)
    return jobs
