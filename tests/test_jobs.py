import sqlite3
import time
import json
from pathlib import Path

import pytest

from app.models.job import AlignmentMode, JobStatus
from app.services.job_manager import JobManager
from app.services.process_runner import ProcessTimeoutError


def create(client, path):
    return client.post("/api/jobs", json={"mediaPath": str(path)})


def wait_terminal(client, job_id):
    for _ in range(200):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"COMPLETED", "FAILED"}:
            return body
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_create_real_analysis_job(client, media_file):
    before = {path.name for path in media_file.parent.iterdir()}
    response = create(client, media_file)
    assert response.status_code == 202
    body = wait_terminal(client, response.json()["jobId"])
    assert body["status"] == "COMPLETED"
    assert body["progress"] == 100
    assert body["resolvedMediaPath"] == str(media_file.resolve())
    assert body["report"]["mediaDirectoryModified"] is False
    assert {path.name for path in media_file.parent.iterdir()} == before
    stages = [event.stage for event in client.app.state.jobs.events(body["jobId"])]
    assert stages == [JobStatus.QUEUED, JobStatus.VALIDATING_PATH, JobStatus.PROBING_MEDIA,
                      JobStatus.DISCOVERING_SUBTITLES, JobStatus.DISCOVERING_SUBTITLES,
                      JobStatus.ANALYZING_CANDIDATES, JobStatus.ANALYZING_CANDIDATES,
                      JobStatus.ANALYZING_CANDIDATES, JobStatus.EXTRACTING_REFERENCE,
                      JobStatus.COMPLETED]


def test_invalid_path_creates_persistent_failed_job(client, settings):
    response = create(client, settings.media_roots[0] / "missing.mkv")
    assert response.status_code == 202
    body = wait_terminal(client, response.json()["jobId"])
    assert body["status"] == "FAILED"
    assert "nie istnieje" in body["errorMessage"]


def test_relative_and_outside_paths_fail_as_jobs(client):
    for path in ("relative.mkv", "/etc/passwd"):
        job_id = create(client, path).json()["jobId"]
        assert wait_terminal(client, job_id)["status"] == "FAILED"


def test_missing_job(client):
    assert client.get("/api/jobs/missing").status_code == 404


def test_list_jobs_and_sse_history(client, media_file, settings):
    job_id = create(client, media_file).json()["jobId"]
    wait_terminal(client, job_id)
    jobs = client.get("/api/jobs").json()
    assert jobs[0]["jobId"] == job_id
    text = client.get(f"/api/jobs/{job_id}/events").text
    assert text.count("event: job") == 10
    assert '"stage":"COMPLETED"' in text
    with sqlite3.connect(settings.data_root / "subtitle-agent.db") as db:
        assert db.execute("SELECT report_json FROM jobs WHERE id=?", (job_id,)).fetchone()[0]


def test_report_survives_manager_restart(client, media_file, settings):
    job_id = create(client, media_file).json()["jobId"]
    wait_terminal(client, job_id)
    restarted = JobManager(settings.data_root / "subtitle-agent.db", settings)
    assert restarted.get(job_id)["report"]["media"]["name"] == media_file.name


def test_restart_marks_running_job_interrupted(settings):
    settings.data_root.mkdir(parents=True)
    manager = JobManager(settings.data_root / "restart.db", settings)
    with manager._connect() as db:
        db.execute("""INSERT INTO jobs
            (id,media_path,status,progress,created_at,started_at,finished_at,error_message,resolved_media_path,report_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("x", "/media/shows/x.mkv", "QUEUED", 0, "2026-01-01T00:00:00+00:00", None, None, None, None, None))
    restarted = JobManager(settings.data_root / "restart.db", settings)
    assert restarted.get("x")["status"] == "INTERRUPTED"


def test_existing_stage_one_database_is_migrated(settings):
    settings.data_root.mkdir(parents=True)
    db_path = settings.data_root / "legacy.db"
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY, media_path TEXT NOT NULL, status TEXT NOT NULL,
            progress INTEGER NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
            finished_at TEXT, error_message TEXT)""")
        db.execute("""CREATE TABLE events (
            job_id TEXT NOT NULL, sequence INTEGER NOT NULL, timestamp TEXT NOT NULL,
            level TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL,
            progress INTEGER NOT NULL, PRIMARY KEY(job_id, sequence))""")
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", (
            "legacy", "/media/shows/example.mkv", "COMPLETED", 100,
            "2026-01-01T00:00:00+00:00", None, "2026-01-01T00:00:03+00:00", None,
        ))
        for sequence, stage in enumerate(("QUEUED", "INSPECTING", "CHECKING_TOOLS", "READY", "COMPLETED"), 1):
            db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (
                "legacy", sequence, "2026-01-01T00:00:00+00:00", "INFO", stage, stage, sequence * 20,
            ))
    manager = JobManager(db_path, settings)
    with manager._connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
    assert {"resolved_media_path", "report_json"}.issubset(columns)
    assert [event.stage for event in manager.events("legacy")] == [
        JobStatus.QUEUED, JobStatus.VALIDATING_PATH, JobStatus.PROBING_MEDIA,
        JobStatus.COMPLETED, JobStatus.COMPLETED,
    ]


def test_unknown_historical_event_does_not_break_history(settings):
    settings.data_root.mkdir(parents=True)
    manager = JobManager(settings.data_root / "unknown.db", settings)
    with manager._connect() as db:
        db.execute("""INSERT INTO jobs
            (id,media_path,status,progress,created_at,started_at,finished_at,error_message,resolved_media_path,report_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                "future", "/media/shows/x.mkv", "COMPLETED", 100,
                "2026-01-01T00:00:00+00:00", None, "2026-01-01T00:00:01+00:00", None, None, None,
            ))
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (
            "future", 1, "2026-01-01T00:00:00+00:00", "INFO",
            "FUTURE_STAGE", "Historyczny wpis", 50,
        ))
    event = manager.events("future")[0]
    assert event.stage == "FUTURE_STAGE"
    assert "FUTURE_STAGE" in event.model_dump_json()


def test_ffmpeg_timeout_has_actionable_job_message(client, media_file, monkeypatch):
    async def timeout(*args, **kwargs):
        raise ProcessTimeoutError("Proces przekroczył limit")

    monkeypatch.setattr("app.services.job_manager.extract_reference", timeout)
    monkeypatch.setattr("app.services.job_manager.rank_english", lambda *args: [{
        "sourceType": "embedded", "streamIndex": 3, "codec": "subrip", "type": "text", "score": 70,
    }])
    job_id = create(client, media_file).json()["jobId"]
    body = wait_terminal(client, job_id)
    assert body["status"] == "FAILED"
    assert "limit 1 s" in body["errorMessage"]
    assert "magazynu sieciowego" in body["errorMessage"]


def _insert_analyzed_job(manager, job_id, report, media_path):
    with manager._connect() as db:
        db.execute("""INSERT INTO jobs
            (id,media_path,status,progress,created_at,started_at,finished_at,error_message,resolved_media_path,report_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                job_id, str(media_path), "COMPLETED", 100, "2026-01-01T00:00:00+00:00", None,
                "2026-01-01T00:00:01+00:00", None, str(media_path), json.dumps(report),
            ))


@pytest.mark.anyio
async def test_graphic_source_finishes_as_needs_ocr(settings, media_file):
    settings.data_root.mkdir(parents=True)
    manager = JobManager(settings.data_root / "ocr.db", settings)
    english = {"sourceType": "embedded", "streamIndex": 2, "codec": "hdmv_pgs_subtitle", "type": "graphic", "score": 70}
    polish = {"sourceType": "external", "name": "movie.pl.srt", "path": str(media_file.parent / "movie.pl.srt"), "score": 80}
    _insert_analyzed_job(manager, "ocr", {"englishRanking": [english], "polishRanking": [polish],
                         "selectedEnglish": english, "selectedPolish": polish}, media_file)
    await manager.align("ocr", None, None)
    job = manager.get("ocr")
    assert job["status"] == "NEEDS_OCR"
    assert job["report"]["alignment"]["status"] == "NEEDS_OCR"


@pytest.mark.anyio
async def test_alignment_preview_persists_and_input_is_unchanged(settings, media_file):
    settings.data_root.mkdir(parents=True)
    manager = JobManager(settings.data_root / "alignment.db", settings)
    work = settings.data_root / "work" / "jobs" / "sync"
    work.mkdir(parents=True)
    english_path, polish_path = work / "reference.srt", media_file.parent / "Example Movie.pl.srt"
    content = "\n\n".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},800\nTekst {i}" for i in range(1, 13)) + "\n"
    english_path.write_text(content, encoding="utf-8")
    polish_path.write_text(content.replace("Tekst", "<i>Polski tekst</i>"), encoding="utf-8")
    before = polish_path.read_bytes()
    english = {"sourceType": "embedded", "streamIndex": 2, "codec": "subrip", "type": "text", "score": 70}
    polish = {"sourceType": "external", "name": polish_path.name, "path": str(polish_path), "format": "srt", "score": 80}
    report = {"media": {"durationSeconds": 30}, "englishRanking": [english], "polishRanking": [polish],
              "selectedEnglish": english, "selectedPolish": polish,
              "workingFiles": {"englishReference": str(english_path)}}
    _insert_analyzed_job(manager, "sync", report, media_file)
    await manager.align("sync", None, None)
    job = manager.get("sync"); alignment = job["report"]["alignment"]
    assert alignment["model"]["strategy"] == "IDENTITY"
    assert alignment["quality"] == "HIGH"
    assert polish_path.read_bytes() == before
    preview = Path(alignment["previewPath"])
    assert preview.is_file() and "<i>Polski tekst</i>" in preview.read_text()
    assert job["report"]["semanticAlignment"]["fallbackUsed"] is True
    restarted = JobManager(settings.data_root / "alignment.db", settings)
    assert restarted.get("sync")["report"]["alignment"]["previewSha256"] == alignment["previewSha256"]


@pytest.mark.anyio
async def test_structural_only_never_constructs_openai_provider(settings, media_file, monkeypatch):
    settings.data_root.mkdir(parents=True)
    manager = JobManager(settings.data_root / "structural.db", settings)
    work = settings.data_root / "work" / "jobs" / "structural"
    work.mkdir(parents=True)
    english_path, polish_path = work / "reference.srt", media_file.parent / "movie.pl.srt"
    content = "\n\n".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},800\nText {i}" for i in range(1, 13)) + "\n"
    english_path.write_text(content); polish_path.write_text(content)
    english = {"sourceType": "embedded", "streamIndex": 1, "type": "text", "score": 70}
    polish = {"sourceType": "external", "name": polish_path.name, "path": str(polish_path), "score": 80}
    report = {"media": {"durationSeconds": 30}, "englishRanking": [english], "polishRanking": [polish],
              "selectedEnglish": english, "selectedPolish": polish,
              "workingFiles": {"englishReference": str(english_path)}}
    _insert_analyzed_job(manager, "structural", report, media_file)
    monkeypatch.setattr("app.services.job_manager.OpenAIAnchorProvider",
                        lambda *_: (_ for _ in ()).throw(AssertionError("OpenAI must not be constructed")))
    await manager.align("structural", None, None, AlignmentMode.STRUCTURAL_ONLY)
    assert manager.get("structural")["status"] == "COMPLETED"


@pytest.mark.anyio
async def test_semantic_required_without_configuration_has_no_structural_result(settings, media_file):
    settings.data_root.mkdir(parents=True)
    manager = JobManager(settings.data_root / "required.db", settings)
    english = {"sourceType": "embedded", "streamIndex": 1, "type": "text", "score": 70}
    polish_path = media_file.parent / "required.pl.srt"; polish_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nPL\n")
    work = settings.data_root / "work" / "jobs" / "required"; work.mkdir(parents=True)
    english_path = work / "reference.srt"; english_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nEN\n")
    polish = {"sourceType": "external", "name": polish_path.name, "path": str(polish_path), "score": 80}
    report = {"media": {"durationSeconds": 30}, "englishRanking": [english], "polishRanking": [polish],
              "selectedEnglish": english, "selectedPolish": polish,
              "workingFiles": {"englishReference": str(english_path)}}
    _insert_analyzed_job(manager, "required", report, media_file)
    await manager.align("required", None, None, AlignmentMode.SEMANTIC_REQUIRED)
    job = manager.get("required")
    assert job["status"] == "AI_UNAVAILABLE" and "alignment" not in job["report"]


def test_preview_endpoint_rejects_recorded_path_outside_job(client, settings, media_file):
    manager = client.app.state.jobs
    _insert_analyzed_job(manager, "unsafe", {"alignment": {"previewPath": "/etc/passwd"}}, media_file)
    response = client.get("/api/jobs/unsafe/preview")
    assert response.status_code == 404


def test_semantic_diagnostics_never_exposes_secret(client):
    response = client.get("/api/jobs/semantic/config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False and payload["configured"] is False
    assert "key" not in str(payload).lower()


def test_publish_api_rejects_arbitrary_target_fields(client, media_file):
    job_id = create(client, media_file).json()["jobId"]
    response = client.post(f"/api/jobs/{job_id}/publication", json={
        "confirmed": True, "expectedPreviewSha256": "0" * 64,
        "targetPath": "/media/movies/overwrite.srt", "version": 1})
    assert response.status_code == 422


def test_publish_diagnostic_is_safe_when_disabled(client):
    response = client.get("/api/jobs/publishing/config")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
