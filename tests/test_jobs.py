import sqlite3
import time

from app.models.job import JobStatus
from app.services.job_manager import JobManager


def create(client, path="/media/shows/example.mkv"):
    return client.post("/api/jobs", json={"mediaPath": path})


def wait_done(client, job_id):
    for _ in range(100):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] == "COMPLETED": return body
        time.sleep(0.01)
    raise AssertionError("job did not complete")


def test_create_and_complete_job(client):
    response = create(client)
    assert response.status_code == 202
    job_id = response.json()["jobId"]
    body = wait_done(client, job_id)
    assert body["progress"] == 100
    events = client.app.state.jobs.events(job_id)
    assert [event.stage for event in events] == [JobStatus.QUEUED, JobStatus.INSPECTING, JobStatus.CHECKING_TOOLS, JobStatus.READY, JobStatus.COMPLETED]
    assert all(0 <= event.progress <= 100 for event in events)
    assert [event.sequence for event in events] == sorted({event.sequence for event in events})


def test_rejects_relative_path(client): assert create(client, "shows/a.mkv").status_code == 422
def test_rejects_outside_root(client): assert create(client, "/etc/passwd").status_code == 422
def test_rejects_traversal(client): assert create(client, "/media/shows/../../etc/passwd").status_code == 422
def test_missing_job(client): assert client.get("/api/jobs/missing").status_code == 404


def test_events_are_persisted_and_sse_replays_history(client, settings):
    job_id = create(client).json()["jobId"]
    wait_done(client, job_id)
    with sqlite3.connect(settings.data_root / "subtitle-agent.db") as db:
        assert db.execute("SELECT count(*) FROM events WHERE job_id=?", (job_id,)).fetchone()[0] == 5
    text = client.get(f"/api/jobs/{job_id}/events").text
    assert text.count("event: job") == 5
    assert '"stage":"COMPLETED"' in text


def test_restart_marks_running_job_interrupted(settings):
    settings.data_root.mkdir(parents=True)
    manager = JobManager(settings.data_root / "restart.db", 1)
    with manager._connect() as db:
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", ("x", "/media/shows/x", "QUEUED", 0, "2026-01-01T00:00:00+00:00", None, None, None))
    restarted = JobManager(settings.data_root / "restart.db", 1)
    assert restarted.get("x")["status"] == "INTERRUPTED"
