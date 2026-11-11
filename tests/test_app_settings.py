import asyncio
import threading

import pytest

from app.services.app_settings import AppSettings, AppSettingsStore
from app.services.subtitle_extraction import SubtitleExtractionResult
from tests.helpers import embedded_reference, wait_workpack


def test_runtime_settings_are_allowlisted_persistent_and_rename_settings_page(client, settings):
    original = settings.model_copy(deep=True)
    current = client.get('/api/settings/app')
    assert current.status_code == 200
    data = current.json()
    assert set(data) == set(AppSettings.model_fields)
    assert data['ffmpeg_timeout_seconds'] == original.ffmpeg_timeout_seconds
    assert not {'auth_password', 'media_roots', 'openai_api_key', 'data_root'} & data.keys()
    data.update(archive_max_titles=12, ffmpeg_timeout_seconds=42, directory_max_items=17, ocr_worker_url='http://ocr:8090/')
    saved = client.put('/api/settings/app', json=data)
    assert saved.status_code == 200, saved.text
    assert saved.json()['ocr_worker_url'] == 'http://ocr:8090'
    assert client.app.state.jobs.settings.ffmpeg_timeout_seconds == 42
    assert client.get('/api/settings/app').json() == saved.json()
    assert client.get('/api/workpacks/config').json()['ocrWorkerEnabled'] is True
    restarted = original.model_copy(deep=True)
    assert AppSettingsStore(settings.data_root / 'subtitle-agent.db', restarted).get() == saved.json()
    assert restarted.ffmpeg_timeout_seconds == 42
    assert restarted.auth_password == original.auth_password
    page = client.get('/settings').text
    assert '<title>Ustawienia ·' in page and '> Ustawienia</a>' in page
    assert 'id="app-settings-panel"' in page
    assert 'id="ai-console"' not in page and 'id="ai-settings"' not in page
    assert '/static/settings.js?' not in page
    ai_page = client.get('/ai')
    assert ai_page.status_code == 200
    assert '<title>AI ·' in ai_page.text and '> AI</a>' in ai_page.text
    assert 'id="ai-settings"' in ai_page.text and 'id="ai-console"' in ai_page.text
    assert 'id="app-settings-panel"' not in ai_page.text
    assert '/static/app-settings.js?' not in ai_page.text
    assert 'href="/ai" class="nav-tab" aria-current="page"' in ai_page.text
    assert 'href="/settings" class="nav-tab" aria-current="page"' in page
    assert all(f'data-setting="{name}"' in page for name in data)


@pytest.mark.parametrize('changes', [
    {'auth_password': 'not-editable'}, {'media_roots': ['/arbitrary']},
    {'max_concurrent_jobs': 4}, {'max_queued_jobs': 8}, {'max_queued_ai_operations': 8},
    {'subtitle_agent_app_mode': 'ADVANCED'}, {'subtitle_agent_publish_enabled': True},
    {'ai_max_saved_models': 0}, {'ai_max_saved_models': 101},
    {'archive_max_titles': 0}, {'archive_max_titles': 10001}, {'archive_max_titles': True},
    {'ffmpeg_timeout_seconds': 0}, {'directory_max_items': 0},
    {'include_graphic_reference': 'false'}, {'workpack_max_archive_bytes': -1},
    {'ocr_worker_url': 'https://outside.invalid'},
    {'ocr_worker_url': 'http://user:password@ocr:8090'},
    {'ocr_worker_url': 'http://ocr:99999'},
])
def test_runtime_settings_reject_unsupported_or_invalid_changes_atomically(client, changes):
    before = client.get('/api/settings/app').json()
    response = client.put('/api/settings/app', json={**before, **changes})
    assert response.status_code == 422
    assert client.get('/api/settings/app').json() == before


def test_runtime_settings_require_session_and_csrf(client):
    data = client.get('/api/settings/app').json()
    assert client.put('/api/settings/app', json=data, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    client.cookies.clear()
    assert client.get('/api/settings/app').status_code == 401
    assert client.put('/api/settings/app', json=data).status_code == 401


def test_running_workpack_keeps_snapshot_and_next_job_uses_saved_settings(client, settings, media_file, monkeypatch):
    started, release = threading.Event(), threading.Event()
    probes, extractions = [], []
    old_probe, old_extract = settings.ffprobe_timeout_seconds, settings.ffmpeg_timeout_seconds

    async def probe(path, timeout):
        probes.append(timeout)
        if len(probes) == 1:
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
        return {'path': str(path), 'name': path.name, 'sizeBytes': path.stat().st_size,
                'durationSeconds': 100, 'embeddedSubtitles': [embedded_reference('text')], 'audioTracks': []}

    async def extract(reference, media_path, target, timeout):
        extractions.append(timeout)
        target.mkdir(parents=True, exist_ok=True)
        output = target / 'selected.eng.srt'
        output.write_text('1\n00:00:01,000 --> 00:00:02,000\nNeutral dialogue.\n')
        return SubtitleExtractionResult([output], [])

    monkeypatch.setattr('app.services.job_manager.probe_media', probe)
    monkeypatch.setattr('app.services.job_manager.extract_embedded', extract)
    first = client.post('/api/tasks', json={'mediaPath': str(media_file), 'mode': 'PREPARE_TRANSLATION'}).json()['jobId']
    try:
        assert started.wait(3)
        values = client.get('/api/settings/app').json()
        values.update(ffprobe_timeout_seconds=44, ffmpeg_timeout_seconds=55)
        assert client.put('/api/settings/app', json=values).status_code == 200
        assert client.app.state.jobs.settings.ffmpeg_timeout_seconds == 55
    finally:
        release.set()
    assert wait_workpack(client, first)['status'] == 'WORKPACK_READY'
    second = client.post('/api/tasks', json={'mediaPath': str(media_file), 'mode': 'PREPARE_TRANSLATION'}).json()['jobId']
    assert wait_workpack(client, second)['status'] == 'WORKPACK_READY'
    assert probes == [old_probe, 44]
    assert extractions == [old_extract, 55]


def test_active_directory_keeps_one_snapshot_for_all_episodes_and_final_zip(client, settings, monkeypatch):
    from app.services import job_manager
    from tests.test_directory_batch import season, configure, submit, wait_batch
    directory = season(settings, 2)
    configure(monkeypatch)
    base_probe = job_manager.probe_media
    started, release = threading.Event(), threading.Event()
    timeouts = []
    old_timeout = settings.ffprobe_timeout_seconds

    async def probe(path, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
        return await base_probe(path, timeout)

    monkeypatch.setattr(job_manager, 'probe_media', probe)
    first = submit(client, directory)
    try:
        assert started.wait(3)
        values = client.get('/api/settings/app').json()
        values.update(ffprobe_timeout_seconds=44, directory_max_items=1, directory_max_files=3)
        assert client.put('/api/settings/app', json=values).status_code == 200
    finally:
        release.set()
    assert wait_batch(client, first)['status'] == 'WORKPACK_READY'
    assert timeouts == [old_timeout, old_timeout]
    second = submit(client, directory)
    assert wait_batch(client, second)['status'] == 'FAILED'


def test_upgrade_loads_old_saved_settings_with_archive_limit_from_environment(settings, monkeypatch):
    import json
    import sqlite3
    from app.core.config import Settings
    settings.data_root.mkdir()
    path = settings.data_root / 'settings.db'
    old_values = AppSettingsStore(path, settings).get()
    old_values.pop('archive_max_titles')
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO app_settings VALUES(1,?)', (json.dumps(old_values),))
    monkeypatch.setenv('ARCHIVE_MAX_TITLES', '7')
    monkeypatch.setenv('WORKPACK_RETENTION_HOURS', 'not-used')
    restarted = Settings(data_root=settings.data_root)
    assert 'workpack_retention_hours' not in Settings.model_fields
    assert AppSettingsStore(path, restarted).get()['archive_max_titles'] == 7
