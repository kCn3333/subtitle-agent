import os
import shutil
import stat
import uuid
from pathlib import Path


def validated_job_directory(jobs_root: Path, candidate: Path) -> Path | None:
    root = jobs_root.resolve()
    if candidate.parent.resolve() != root:
        return None
    try:
        uuid.UUID(candidate.name)
        mode = candidate.lstat().st_mode
    except (ValueError, OSError):
        return None
    return candidate if stat.S_ISDIR(mode) and not candidate.is_symlink() else None


def contains_symlink(directory: Path) -> bool:
    for current, directories, files in os.walk(directory, topdown=True, followlinks=False):
        base = Path(current)
        for name in (*directories, *files):
            try:
                if (base / name).is_symlink():
                    return True
            except OSError:
                return True
    return False


def remove_job_directory(jobs_root: Path, candidate: Path) -> bool:
    directory = validated_job_directory(jobs_root, candidate)
    if directory is None or contains_symlink(directory):
        return False
    shutil.rmtree(directory)
    return True


def remove_previous_archives(jobs_root: Path, job_dir: Path) -> int:
    directory = validated_job_directory(jobs_root, job_dir)
    if directory is None:
        return 0
    removed = 0
    for entry in directory.iterdir():
        if entry.parent != directory or entry.is_symlink() or not entry.is_file() or entry.suffix.lower() != ".zip":
            continue
        entry.unlink()
        removed += 1
    return removed
