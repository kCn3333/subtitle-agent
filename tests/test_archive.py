import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.job_manager import JobManager
from app.core.config import Settings


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


def test_archive_keeps_versions_deduplicates_sha_and_preserves_srt_on_retry(tmp_path):
    manager = manager_at(tmp_path)
    job, directory = add_job(manager, 'Film')
    manager.archive.capture(manager.get(job))
    # Repeated identical workpack is not added twice, nor copied twice.
    second, _ = add_job(manager, 'Film', 1)
    manager.archive.capture(manager.get(second))
    different = directory / 'version2.zip'; different.write_bytes(b'new zip')
    manager.archive.record(manager.get(job), different, 'workpack')
    output = directory / 'ai-sync.pl.srt'; output.write_text('1\n00:00:01,000 --> 00:00:02,000\nPL\n')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (directory / 'ai-sync.json').write_text(json.dumps({'sha256': digest}))
    manager.archive.capture(manager.get(job))
    output.unlink(); (directory / 'ai-sync.json').unlink()
    manager.cleanup_expired_artifacts()
    entries = manager.archive.list_titles(manager.list_jobs(limit=None))
    assert len(entries) == 1 and len(entries[0]['workpacks']) == 2
    assert len(entries[0]['subtitles']) == 1
    assert len(list(manager.archive.root.glob('*.zip'))) == 2
    assert manager.archive.download(digest + '.srt')
    restarted = JobManager(manager.db_path, manager.settings)
    assert restarted.archive.list_titles(restarted.list_jobs())[0]['subtitles'] == entries[0]['subtitles']


def test_retention_removes_old_title_jobs_events_files_and_unreferenced_blobs(tmp_path):
    manager = manager_at(tmp_path)
    old, old_dir = add_job(manager, 'Old', payload=b'old unique')
    duplicate, duplicate_dir = add_job(manager, 'Old', 1, payload=b'old version')
    with manager._connect() as db:
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)', (old, 1, '2025', 'INFO', 'WORKPACK_READY', 'ready', 100))
    manager.archive.capture(manager.get(old))
    for index in range(30):
        add_job(manager, f'Film{index}', index + 2, payload=f'zip{index}'.encode())
    assert manager.cleanup_expired_artifacts() == 2
    assert manager.get(old) is None and manager.get(duplicate) is None
    assert not old_dir.exists() and not duplicate_dir.exists()
    assert manager.events(old) == []
    assert len(manager.archive.list_titles(manager.list_jobs(limit=None))) == 30
    assert len(list(manager.archive.root.glob('*.zip'))) == 30
    assert len({j['media_path'] for j in manager.list_jobs(limit=None)}) == 30


def test_active_ai_is_pinned_until_finished_and_shared_sha_survives_pruning(tmp_path):
    manager = manager_at(tmp_path)
    old, directory = add_job(manager, 'Old')
    manager.artifact_users.add(old)
    for index in range(30):
        add_job(manager, f'New{index}', index + 1)
    manager.cleanup_expired_artifacts()
    assert directory.exists()
    manager.artifact_users.discard(old)
    manager.cleanup_expired_artifacts()
    assert not directory.exists()
    assert len(list(manager.archive.root.glob('*.zip'))) == 1


def test_sha_validation_and_path_boundaries(tmp_path):
    manager = manager_at(tmp_path)
    job, directory = add_job(manager, 'Film')
    manager.archive.capture(manager.get(job))
    external = tmp_path / 'outside.srt'; external.write_text('private')
    manager.archive.record(manager.get(job), external, 'translation')
    linked = directory / 'link.srt'; linked.symlink_to(external)
    manager.archive.record(manager.get(job), linked, 'translation')
    assert not list(manager.archive.root.glob('*.srt'))
    assert manager.archive.download('../subtitle-agent.db') is None
    name = next(manager.archive.root.glob('*.zip'))
    name.write_bytes(b'corrupted')
    assert manager.archive.download(name.name) is None


def test_archive_api_and_page(client):
    manager = client.app.state.jobs
    job, directory = add_job(manager, '<film>')
    manager.archive.capture(manager.get(job))
    assert client.get('/archive').status_code == 200
    entries = client.get('/api/archive').json()['titles']
    assert entries[0]['title'] == '<film>'
    assert '/media/' not in json.dumps(entries)
    download = client.get(entries[0]['workpacks'][0]['url'])
    assert download.status_code == 200 and download.content == b'zip'
    assert client.get('/api/archive/files/not-a-hash.zip').status_code == 404


def test_migration_reads_more_than_public_job_list_limit(tmp_path):
    manager = manager_at(tmp_path)
    oldest, directory = add_job(manager, 'Old')
    for i in range(501):
        add_job(manager, 'Repeated', i + 1)
    for i in range(29):
        add_job(manager, f'New{i}', i + 502)
    manager.cleanup_expired_artifacts()
    assert not directory.exists()
    assert len(manager.archive.list_titles(manager.list_jobs(limit=None))) == 30
