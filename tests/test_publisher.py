import asyncio
import os
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.alignment import sha256
from app.services.publisher import (PublishConflict, PublishPermissionDenied, PublishSourceChanged,
                                    PublishUnsupportedFilesystem, SubtitlePublisher)
from app.services.job_manager import JobManager
from app.services.publisher import identity


def configured(tmp_path: Path, **updates):
    tmp_path.mkdir(parents=True, exist_ok=True)
    library = tmp_path / "library"; library.mkdir(exist_ok=True)
    values = {"data_root": tmp_path / "data", "media_roots": [library],
              "subtitle_agent_publish_enabled": True, "subtitle_agent_publish_mode": "MANUAL",
              "subtitle_agent_publish_mappings_json": {library: library}}
    values.update(updates)
    return Settings(**values), library


def media(library: Path, folder="Film (2000)", name="Film (2000) [1080p].mkv") -> Path:
    directory = library / folder; directory.mkdir(parents=True, exist_ok=True)
    path = directory / name; path.write_bytes(b"video")
    return path


def preview(tmp_path: Path) -> Path:
    path = tmp_path / "preview.srt"; path.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nTekst\n")
    return path


def test_publishing_defaults_disabled_and_preview_only(tmp_path):
    settings = Settings(data_root=tmp_path)
    assert settings.subtitle_agent_publish_enabled is False
    assert settings.subtitle_agent_publish_mode == "PREVIEW_ONLY"
    assert SubtitlePublisher(settings).diagnostic()["enabled"] is False


def test_disabled_diagnostic_tolerates_missing_publish_mounts(tmp_path):
    settings = Settings(data_root=tmp_path, subtitle_agent_publish_mappings_json={
        tmp_path / "media": tmp_path / "missing-publish"})
    result = SubtitlePublisher(settings).diagnostic()
    assert result["mappings"][0]["publishRootExists"] is False


def test_enabled_diagnostic_probes_root_and_cleans_files(tmp_path):
    settings, library = configured(tmp_path)
    result = SubtitlePublisher(settings).diagnostic()
    assert result["mappings"][0]["atomicPublishSupported"] is True
    assert not list(library.glob(".subtitle-agent-diagnostic-*"))


@pytest.mark.parametrize("folder", ["movies/Film", "shows/Series/Season 1"])
def test_mapping_movies_and_shows(tmp_path, folder):
    settings, library = configured(tmp_path)
    movie = media(library, folder)
    plan = SubtitlePublisher(settings).plan(movie)
    assert Path(plan.target_directory) == movie.parent


def test_longest_matching_media_root_wins(tmp_path):
    settings, library = configured(tmp_path)
    nested = library / "nested"; nested.mkdir()
    settings.subtitle_agent_publish_mappings_json = {library: library, nested: nested}
    movie = media(nested, "Film")
    assert SubtitlePublisher(settings).plan(movie).media_root == str(nested.resolve())


def test_path_outside_mapping_and_symlink_escape_are_rejected(tmp_path):
    settings, library = configured(tmp_path); publisher = SubtitlePublisher(settings)
    outside = tmp_path / "outside"; outside.mkdir(); movie = media(outside)
    with pytest.raises(PublishConflict): publisher.plan(movie)
    escape = library / "escape"; escape.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PublishConflict): publisher.plan(escape / movie.parent.name / movie.name)


def test_mismatched_ro_rw_mounts_are_rejected(tmp_path):
    ro = tmp_path / "ro"; rw = tmp_path / "rw"; ro.mkdir(); rw.mkdir()
    movie = media(ro); (rw / movie.parent.name).mkdir(); (rw / movie.parent.name / movie.name).write_bytes(b"video")
    settings = Settings(data_root=tmp_path / "data", media_roots=[ro],
                        subtitle_agent_publish_mappings_json={ro: rw})
    with pytest.raises(PublishConflict, match="samego katalogu"):
        SubtitlePublisher(settings).plan(movie)


def test_versioned_name_collision_symlink_and_exhaustion(tmp_path):
    settings, library = configured(tmp_path, subtitle_agent_publish_max_version=2)
    movie = media(library); publisher = SubtitlePublisher(settings)
    first = publisher.plan(movie)
    assert first.target_name == "Film (2000) [1080p].AI-Sync-v001.pl.srt"
    Path(first.target_path).write_text("existing")
    second = publisher.plan(movie); assert second.version == 2
    Path(second.target_path).symlink_to(tmp_path / "missing")
    with pytest.raises(PublishConflict, match="Wyczerpano"):
        publisher.plan(movie)


def test_atomic_publication_preserves_every_existing_file_and_mode(tmp_path):
    settings, library = configured(tmp_path); movie = media(library); source = preview(tmp_path)
    existing = movie.parent / "user.pl.srt"; existing.write_bytes(b"user subtitle")
    publisher = SubtitlePublisher(settings); result = publisher.publish(publisher.plan(movie), source, sha256(source))
    target = Path(result["targetPath"])
    assert target.read_bytes() == source.read_bytes()
    assert existing.read_bytes() == b"user subtitle"
    assert target.stat().st_mode & 0o777 == 0o644
    assert not list(movie.parent.glob(".subtitle-agent-*.tmp"))


def test_changed_preview_is_rejected_without_writes(tmp_path):
    settings, library = configured(tmp_path); movie = media(library); source = preview(tmp_path)
    plan = SubtitlePublisher(settings).plan(movie); old_hash = sha256(source); source.write_bytes(b"changed")
    with pytest.raises(PublishSourceChanged): SubtitlePublisher(settings).publish(plan, source, old_hash)
    assert not Path(plan.target_path).exists()


def test_unsupported_hard_link_cleans_own_temporary_file(tmp_path, monkeypatch):
    settings, library = configured(tmp_path); movie = media(library); source = preview(tmp_path)
    def unsupported(*args, **kwargs): raise OSError(os.errno.ENOTSUP if hasattr(os, "errno") else 95, "unsupported")
    monkeypatch.setattr(os, "link", unsupported)
    with pytest.raises(PublishUnsupportedFilesystem):
        SubtitlePublisher(settings).publish(SubtitlePublisher(settings).plan(movie), source, sha256(source))
    assert not list(movie.parent.glob(".subtitle-agent-*.tmp"))


def test_permission_denied_is_controlled(tmp_path, monkeypatch):
    settings, library = configured(tmp_path); movie = media(library); source = preview(tmp_path)
    original = os.open
    def denied(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(".subtitle-agent-"): raise PermissionError("denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(PublishPermissionDenied):
        SubtitlePublisher(settings).publish(SubtitlePublisher(settings).plan(movie), source, sha256(source))


@pytest.mark.anyio
async def test_two_concurrent_publications_choose_distinct_versions(tmp_path):
    settings, library = configured(tmp_path); movie = media(library); source = preview(tmp_path)
    publisher = SubtitlePublisher(settings)
    results = await asyncio.gather(*[
        asyncio.to_thread(publisher.publish, publisher.plan(movie), source, sha256(source)) for _ in range(2)])
    assert {item["version"] for item in results} == {1, 2}
    assert all(Path(item["targetPath"]).exists() for item in results)


def test_json_mapping_and_octal_mode_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("SUBTITLE_AGENT_PUBLISH_MAPPINGS_JSON", '{"/media/movies":"/publish/movies"}')
    monkeypatch.setenv("SUBTITLE_AGENT_PUBLISH_FILE_MODE", "0640")
    settings = Settings(data_root=tmp_path)
    assert settings.subtitle_agent_publish_mappings_json[Path("/media/movies")] == Path("/publish/movies")
    assert settings.subtitle_agent_publish_file_mode == 0o640


def aligned_manager(tmp_path: Path, quality="HIGH", semantic=True, fallback=False):
    settings, library = configured(tmp_path); settings.data_root.mkdir()
    manager = JobManager(settings.data_root / "publish.db", settings)
    movie = media(library); polish = movie.parent / "source.pl.srt"; polish.write_bytes(b"source")
    work = settings.data_root / "work" / "jobs" / "job"; work.mkdir(parents=True)
    source = preview(work); preview_path = work / "preview.AI-Sync.pl.srt"; source.rename(preview_path)
    alignment = {"status": "COMPLETED", "quality": quality, "warnings": [], "previewPath": str(preview_path),
                 "previewSha256": sha256(preview_path), "inputSha256": sha256(polish),
                 "mediaIdentity": identity(movie), "selectedPolish": {"path": str(polish)}}
    report = {"alignment": alignment, "semanticAlignment": {
        "fallbackUsed": fallback, "usage": {"accepted_anchors": 2 if semantic else 0}}}
    with manager._connect() as db:
        db.execute("""INSERT INTO jobs
            (id,media_path,status,progress,created_at,started_at,finished_at,error_message,resolved_media_path,report_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", ("job", str(movie), "COMPLETED", 100,
            "2026-01-01T00:00:00+00:00", None, "2026-01-01T00:00:01+00:00", None, str(movie),
            __import__("json").dumps(report)))
    return manager, settings, movie, polish, preview_path


@pytest.mark.anyio
async def test_manual_medium_idempotency_audit_and_restart(tmp_path):
    manager, settings, _, _, preview_path = aligned_manager(tmp_path, quality="MEDIUM")
    first = await manager.publish("job", True, sha256(preview_path))
    second = await manager.publish("job", True, sha256(preview_path))
    assert first == second and first["status"] == "PUBLISHED"
    assert len(manager.publication_attempts("job")) == 1
    restarted = JobManager(settings.data_root / "publish.db", settings)
    assert restarted.get("job")["report"]["publication"]["targetPath"] == first["targetPath"]
    assert restarted.get("job")["status"] == "PUBLISHED"


@pytest.mark.anyio
async def test_two_concurrent_requests_for_same_job_are_idempotent(tmp_path):
    manager, _, _, _, preview_path = aligned_manager(tmp_path)
    digest = sha256(preview_path)
    first, second = await asyncio.gather(
        manager.publish("job", True, digest), manager.publish("job", True, digest))
    assert first["targetPath"] == second["targetPath"]
    assert len(manager.publication_attempts("job")) == 1


@pytest.mark.anyio
async def test_low_manual_and_medium_auto_are_blocked(tmp_path):
    manager, _, _, _, preview_path = aligned_manager(tmp_path, quality="LOW")
    with pytest.raises(Exception, match="Jakość"): await manager.publish("job", True, sha256(preview_path))
    manager, settings, _, _, preview_path = aligned_manager(tmp_path / "auto", quality="MEDIUM")
    settings.subtitle_agent_publish_mode = "AUTO_HIGH"
    with pytest.raises(Exception, match="Jakość"): await manager.publish("job", True, sha256(preview_path), automatic=True)


@pytest.mark.anyio
async def test_auto_requires_semantic_and_rejects_fallback(tmp_path):
    manager, settings, _, _, preview_path = aligned_manager(tmp_path, semantic=False)
    settings.subtitle_agent_publish_mode = "AUTO_HIGH"
    with pytest.raises(Exception, match="kotwic"): await manager.publish("job", True, sha256(preview_path), automatic=True)
    manager, settings, _, _, preview_path = aligned_manager(tmp_path / "fallback", fallback=True)
    settings.subtitle_agent_publish_mode = "AUTO_HIGH"
    with pytest.raises(Exception, match="fallbackiem"): await manager.publish("job", True, sha256(preview_path), automatic=True)


@pytest.mark.anyio
async def test_auto_high_with_semantic_publishes(tmp_path):
    manager, settings, _, _, preview_path = aligned_manager(tmp_path)
    settings.subtitle_agent_publish_mode = "AUTO_HIGH"
    result = await manager.publish("job", True, sha256(preview_path), automatic=True)
    assert result["status"] == "PUBLISHED" and result["automatic"] is True


@pytest.mark.anyio
async def test_changed_movie_and_polish_source_are_blocked(tmp_path):
    manager, _, movie, _, preview_path = aligned_manager(tmp_path)
    movie.write_bytes(b"changed video")
    with pytest.raises(PublishSourceChanged, match="Film"): await manager.publish("job", True, sha256(preview_path))
    manager, _, _, polish, preview_path = aligned_manager(tmp_path / "polish")
    polish.write_bytes(b"changed subtitle")
    with pytest.raises(PublishSourceChanged, match="SRT"): await manager.publish("job", True, sha256(preview_path))


@pytest.mark.anyio
async def test_database_filesystem_conflict_is_persisted(tmp_path):
    manager, _, _, _, preview_path = aligned_manager(tmp_path)
    result = await manager.publish("job", True, sha256(preview_path)); Path(result["targetPath"]).unlink()
    with pytest.raises(PublishConflict): await manager.publish("job", True, sha256(preview_path))
    assert manager.get("job")["report"]["publication"]["status"] == "PUBLISH_CONFLICT"
