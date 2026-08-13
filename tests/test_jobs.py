import sqlite3
import time

from app.models.job import JobStatus
from app.services.job_manager import JobManager


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
                      JobStatus.DISCOVERING_SUBTITLES, JobStatus.ANALYZING_CANDIDATES,
                      JobStatus.EXTRACTING_REFERENCE, JobStatus.COMPLETED]


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
    assert text.count("event: job") == 7
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
