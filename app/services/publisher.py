import errno
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.config import Settings
from app.services.alignment import sha256


class PublishError(RuntimeError):
    status = "PUBLISH_FAILED"


class PublishDisabled(PublishError): status = "PUBLISH_DISABLED"
class PublishBlockedQuality(PublishError): status = "PUBLISH_BLOCKED_QUALITY"
class PublishSourceChanged(PublishError): status = "PUBLISH_SOURCE_CHANGED"
class PublishConflict(PublishError): status = "PUBLISH_CONFLICT"
class PublishPermissionDenied(PublishError): status = "PUBLISH_PERMISSION_DENIED"
class PublishUnsupportedFilesystem(PublishError): status = "PUBLISH_UNSUPPORTED_FILESYSTEM"


@dataclass
class PublishPlan:
    media_path: str
    media_root: str
    publish_root: str
    target_directory: str
    target_name: str
    target_path: str
    version: int


def identity(path: Path) -> dict:
    info = path.stat()
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtimeNs": info.st_mtime_ns}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class SubtitlePublisher:
    _locks_guard = threading.Lock()
    _locks: dict[str, threading.Lock] = {}

    def __init__(self, settings: Settings):
        self.settings = settings

    @classmethod
    def lock_for(cls, key: str) -> threading.Lock:
        with cls._locks_guard:
            return cls._locks.setdefault(key, threading.Lock())

    def mapping(self, media_path: Path) -> tuple[Path, Path, Path]:
        media = media_path.resolve(strict=True)
        candidates = []
        for configured_source, configured_target in self.settings.subtitle_agent_publish_mappings_json.items():
            source = configured_source.resolve(strict=True)
            if _inside(media, source):
                candidates.append((source, configured_target, media.relative_to(source)))
        if not candidates:
            raise PublishConflict("Ścieżka filmu nie należy do skonfigurowanego mapowania")
        return max(candidates, key=lambda item: len(item[0].parts))

    def plan(self, media_path: Path) -> PublishPlan:
        media_root, configured_publish, relative = self.mapping(media_path)
        try:
            publish_root = configured_publish.resolve(strict=True)
            target_directory = (publish_root / relative.parent).resolve(strict=True)
        except FileNotFoundError as exc:
            raise PublishPermissionDenied("Katalog publikacji nie istnieje") from exc
        if not _inside(target_directory, publish_root):
            raise PublishConflict("Katalog docelowy wychodzi poza publish root")
        ro_directory = (media_root / relative.parent).resolve(strict=True)
        rw_media = target_directory / relative.name
        try:
            if (ro_directory.stat().st_dev, ro_directory.stat().st_ino) != (
                    target_directory.stat().st_dev, target_directory.stat().st_ino):
                raise PublishConflict("Mounty RO i RW nie wskazują tego samego katalogu")
            if identity(media_path.resolve(strict=True)) != identity(rw_media.resolve(strict=True)):
                raise PublishConflict("Mounty RO i RW nie wskazują tego samego filmu")
        except FileNotFoundError as exc:
            raise PublishConflict("Film nie jest widoczny przez mount publikacji") from exc
        stem = media_path.stem
        for version in range(1, self.settings.subtitle_agent_publish_max_version + 1):
            name = f"{stem}.AI-Sync-v{version:03d}.pl.srt"
            candidate = target_directory / name
            try:
                candidate.lstat()
            except FileNotFoundError:
                return PublishPlan(str(media_path), str(media_root), str(publish_root), str(target_directory),
                                   name, str(candidate), version)
        raise PublishConflict("Wyczerpano dostępne numery wersji")

    def diagnostic(self) -> dict:
        mappings = []
        for source, target in self.settings.subtitle_agent_publish_mappings_json.items():
            item = {"mediaRoot": str(source), "publishRoot": str(target), "publishRootExists": target.is_dir(),
                    "writable": os.access(target, os.W_OK) if target.exists() else False}
            item["atomicPublishSupported"] = None
            if self.settings.subtitle_agent_publish_enabled and item["publishRootExists"] and item["writable"]:
                directory_fd = None; temporary = f".subtitle-agent-diagnostic-{secrets.token_hex(12)}.tmp"
                linked = f"{temporary}.link"
                try:
                    directory_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
                    os.close(fd)
                    os.link(temporary, linked, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                            follow_symlinks=False)
                    os.fsync(directory_fd); item["atomicPublishSupported"] = True
                except OSError:
                    item["atomicPublishSupported"] = False
                finally:
                    if directory_fd is not None:
                        for name in (linked, temporary):
                            try: os.unlink(name, dir_fd=directory_fd)
                            except FileNotFoundError: pass
                        os.close(directory_fd)
            mappings.append(item)
        return {"enabled": self.settings.subtitle_agent_publish_enabled,
                "mode": self.settings.subtitle_agent_publish_mode,
                "configured": bool(self.settings.subtitle_agent_publish_mappings_json), "mappings": mappings,
                "process": {"uid": os.geteuid(), "gid": os.getegid()},
                "note": "Test atomowy używa wyłącznie własnych ukrytych plików w publish root i natychmiast je usuwa."}

    def publish(self, plan: PublishPlan, preview: Path, expected_hash: str) -> dict:
        if sha256(preview) != expected_hash:
            raise PublishSourceChanged("SHA-256 preview zmienił się od synchronizacji")
        data = preview.read_bytes()
        directory = Path(plan.target_directory)
        lock = self.lock_for(str(directory / Path(plan.media_path).name))
        with lock:
            # Re-plan under the per-film lock so concurrent jobs choose different versions.
            current = self.plan(Path(plan.media_path))
            directory_fd = None; temporary_name = f".subtitle-agent-{secrets.token_hex(16)}.tmp"
            try:
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                fd = os.open(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             self.settings.subtitle_agent_publish_file_mode, dir_fd=directory_fd)
                try:
                    with os.fdopen(fd, "wb", closefd=True) as handle:
                        handle.write(data); handle.flush(); os.fsync(handle.fileno())
                    os.chmod(temporary_name, self.settings.subtitle_agent_publish_file_mode,
                             dir_fd=directory_fd, follow_symlinks=False)
                    os.link(temporary_name, current.target_name, src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd, follow_symlinks=False)
                    os.fsync(directory_fd)
                finally:
                    try: os.unlink(temporary_name, dir_fd=directory_fd)
                    except FileNotFoundError: pass
            except FileExistsError as exc:
                raise PublishConflict("Nazwa docelowa została zajęta podczas publikacji") from exc
            except PermissionError as exc:
                raise PublishPermissionDenied("Proces nie ma prawa zapisu w katalogu publikacji") from exc
            except OSError as exc:
                if exc.errno in {errno.EOPNOTSUPP, errno.ENOTSUP, errno.EXDEV, errno.EPERM}:
                    raise PublishUnsupportedFilesystem("System plików nie obsługuje bezpiecznego hard link") from exc
                raise PublishError(f"Bezpieczna publikacja nie powiodła się ({exc.errno})") from exc
            finally:
                if directory_fd is not None: os.close(directory_fd)
            target = Path(current.target_path)
            published_hash = sha256(target)
            if published_hash != expected_hash:
                raise PublishConflict("Hash opublikowanego pliku jest niezgodny")
            return {"attemptedAt": datetime.now().astimezone().isoformat(), "result": "PUBLISHED",
                    "targetPath": str(target), "targetName": current.target_name, "version": current.version,
                    "previewSha256": expected_hash, "publishedSha256": published_hash,
                    "sizeBytes": len(data), "mediaIdentity": identity(Path(plan.media_path))}
