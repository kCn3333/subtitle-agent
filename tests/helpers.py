"""Shared test data builders and HTTP polling."""
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.services.job_manager import JobManager


def cue_file(path, text='  Zażółć <i>gęślą</i>\n- Druga kwestia.  '):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'7\n00:00:01,000 --> 00:00:02,000\n{text}\n', encoding='utf-8')
    return path


def add_job(manager, title, index=0, payload=b'zip', status='WORKPACK_READY'):
    job_id = str(uuid.uuid4())
    directory = manager.settings.data_root / 'work' / 'jobs' / job_id
    directory.mkdir(parents=True)
    archive = directory / (title + '.zip')
    archive.write_bytes(payload)
    stamp = (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)).isoformat()
    report = {'workpack': {'path': str(archive), 'filename': archive.name,
                          'sha256': hashlib.sha256(payload).hexdigest()}}
    with manager._connect() as db:
        db.execute('''INSERT INTO jobs(id,media_path,status,progress,created_at,finished_at,report_json,job_type)
                      VALUES(?,?,?,?,?,?,?,?)''',
                   (job_id, '/media/' + title + '.mkv', status, 100, stamp,
                    None if status == 'QUEUED' else stamp, json.dumps(report), 'PREPARE_WORKPACK'))
    return job_id, directory


def manager_at(tmp_path):
    root = tmp_path / 'data'; root.mkdir()
    return JobManager(root / 'subtitle-agent.db', Settings(data_root=root, media_roots=[tmp_path]))


def wait_status(client, path, terminal, timeout=5):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
        body = response.json()
        if body['status'] in terminal:
            return body
        time.sleep(.01)
    raise AssertionError(f"{path} did not finish within {timeout}s; last state: {body}")


def wait_workpack(client, job_id: str) -> dict:
    return wait_status(client, f'/api/workpacks/{job_id}', {
        'INSPECTION_READY', 'WORKPACK_READY', 'WORKPACK_INCOMPLETE', 'NEEDS_OCR', 'FAILED',
    })


def wait_operation(client, operation_id, timeout=5):
    return wait_status(client, f'/api/ai-operations/{operation_id}', {'completed', 'failed'}, timeout)


def embedded_reference(subtitle_type: str = "text") -> dict:
    return {"streamIndex": 2, "subtitleOrder": 0, "codec": "subrip" if subtitle_type == "text" else "hdmv_pgs_subtitle",
            "language": "eng", "title": "English Full Dialogue", "default": True, "forced": False,
            "hearingImpaired": False, "type": subtitle_type}
