import asyncio
import io
import json
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.ai_sync import AiSyncError
from app.services.system_probe import ToolInfo
from app.services.workpack import validate_zip
from tests.conftest import authenticate
from tests.helpers import wait_operation
from tests.test_directory_batch import configure, season, submit, wait_batch


def choices(client, job_id):
    response = client.get(f'/api/tasks/{job_id}/ai-inputs')
    assert response.status_code == 200, response.text
    return {'items': [{'job_id': row['job_id'], 'reference_source_id': row['reference_source_id'],
                       'polish_file': row['polish_candidates'][0]['archiveName'] if row['polish_candidates'] else None}
                      for row in response.json()['items'] if row['available']]}


def start_ai(client, job_id, payload, endpoint='ai-sync'):
    response = client.post(f'/api/tasks/{job_id}/{endpoint}', json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def model(monkeypatch, fail_episode=None, calls=None):
    calls = calls if calls is not None else []
    async def reply(settings, instruction, data):
        from app.services.ai_console import record_metrics
        reference = (data.get('english') or data.get('reference'))[0]['text']
        calls.append(reference)
        usage = {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5, 'cost': .01, 'cost_currency': 'USD'}
        record_metrics(.1, usage)
        if fail_episode and fail_episode in reference:
            raise AiSyncError('Błąd modelu dla odcinka', .1, usage)
        if 'polish' in data:
            episode = int(reference.split('S01E')[1][:2])
            assert f'odcinka {episode}.' in data['polish'][0]['text']
            result = {'segments': [{'id': item['id'], 'start_ms': 3000, 'end_ms': 4000} for item in data['polish']]}
        else:
            result = {'segments': [{'id': item['id'], 'text': 'Polskie tłumaczenie.'} for item in data.get('english', data.get('reference'))]}
        return result, .1, usage
    monkeypatch.setattr('app.api.ai_sync.chat_request', reply)
    return calls


@pytest.mark.parametrize('mode,endpoint,count', [('PREPARE_SYNC','ai-sync',7), ('PREPARE_TRANSLATION','ai-translate',2)])
def test_directory_ai_uses_isolated_inputs_and_one_queue_slot_with_archived_zip(client, settings, monkeypatch, tmp_path, mode, endpoint, count):
    directory = season(settings, count)
    configure(monkeypatch)
    job_id = submit(client, directory, mode)
    assert wait_batch(client, job_id)['status'] == 'WORKPACK_READY'
    calls = model(monkeypatch)
    payload = choices(client, job_id)
    payload['items'].reverse()
    state = start_ai(client, job_id, payload, endpoint)
    done = wait_operation(client, state['operation_id'])
    assert done['status'] == 'completed', done
    assert done['result']['file_count'] == count
    assert done['result']['failed_count'] == 0
    assert done['result']['usage']['total_tokens'] == count * 5
    assert len(calls) == count
    assert 'E01.' in calls[0]
    assert len(client.app.state.ai_queue.operations) == 1
    downloaded = client.get(f'/api/tasks/{job_id}/{endpoint}/download')
    assert downloaded.status_code == 200
    result_file = tmp_path / 'results.zip'; result_file.write_bytes(downloaded.content)
    validate_zip(result_file)
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        assert manifest['complete'] is True
        assert len(manifest['items']) == count
        assert str(settings.media_roots[0]) not in json.dumps(manifest)
        outputs = [bundle.read(item['output']['name']).decode() for item in manifest['items']]
        if mode == 'PREPARE_SYNC':
            for index, text in enumerate(outputs, 1):
                assert f'odcinka {index}.' in text and '00:00:03,000' in text
        else:
            assert all('Polskie tłumaczenie.' in text and '00:00:01,000' in text for text in outputs)
    titles = client.get('/api/archive').json()['titles']
    assert len(titles) == 1
    archived = titles[0]['subtitles'][0]
    assert archived['filename'].endswith('.zip')
    assert client.get(archived['url']).content == downloaded.content
    assert titles[0]['cost']['requests'] == count
    assert client.get(f'/api/tasks/{job_id}/{endpoint}').json()['result']['sha256'] == done['result']['sha256']


def test_user_skip_is_completed_and_ai_only_receives_remaining_episodes(client, settings, monkeypatch):
    directory = season(settings, 3)
    configure(monkeypatch, ambiguous=True)
    job_id = submit(client, directory)
    assert wait_batch(client, job_id)['status'] == 'BATCH_PAUSED'
    client.post(f'/api/tasks/{job_id}/batch', json={'action': 'skip'})
    body = wait_batch(client, job_id)
    assert body['status'] == 'WORKPACK_READY' and body['progress'] == 100
    assert body['report']['warnings'] == []
    calls = model(monkeypatch)
    state = start_ai(client, job_id, choices(client, job_id))
    assert wait_operation(client, state['operation_id'])['status'] == 'completed'
    assert len(calls) == 2 and not any('E01.' in call for call in calls)


def test_existing_batches_with_only_user_skips_are_normalized_on_read(client, settings, monkeypatch):
    directory = season(settings, 1)
    configure(monkeypatch, external=True)
    job_id = submit(client, directory)
    wait_batch(client, job_id)
    client.post(f'/api/tasks/{job_id}/batch', json={'action': 'skip'})
    body = wait_batch(client, job_id)
    report = body['report']
    report['warnings'] = ['Paczka zawiera pozycje niekompletne, pominięte lub błędne; sprawdź manifest.']
    with client.app.state.jobs._connect() as db:
        db.execute('UPDATE jobs SET status=?,report_json=? WHERE id=?', ('WORKPACK_INCOMPLETE', json.dumps(report), job_id))
    reloaded = client.get(f'/api/tasks/{job_id}').json()
    assert reloaded['status'] == 'WORKPACK_READY'
    assert reloaded['report']['warnings'] == []
    assert not any(row['available'] for row in client.get(f'/api/tasks/{job_id}/ai-inputs').json()['items'])


def test_invalid_or_changed_batch_inputs_are_rejected_before_any_model_call(client, settings, monkeypatch):
    directory = season(settings, 2)
    configure(monkeypatch)
    job_id = submit(client, directory)
    wait_batch(client, job_id)
    calls = model(monkeypatch)
    payload = choices(client, job_id)
    for invalid in (
        {'items': payload['items'][:1]},
        {'items': [payload['items'][0], payload['items'][0]]},
        {'items': [dict(payload['items'][0], job_id='unrelated'), payload['items'][1]]},
        {'items': [dict(payload['items'][0], reference_source_id='embedded:999'), payload['items'][1]]},
        {'items': [dict(payload['items'][0], polish_file='../private.srt'), payload['items'][1]]},
    ):
        assert client.post(f'/api/tasks/{job_id}/ai-sync', json=invalid).status_code == 422
    child = settings.data_root / 'work' / 'jobs' / payload['items'][1]['job_id']
    (child / payload['items'][1]['polish_file']).write_text('changed')
    assert client.post(f'/api/tasks/{job_id}/ai-sync', json=payload).status_code == 422
    assert calls == []


def test_partial_ai_failure_can_download_and_retry_without_repaying_completed_episodes(client, settings, monkeypatch):
    directory = season(settings, 3)
    configure(monkeypatch)
    job_id = submit(client, directory)
    wait_batch(client, job_id)
    first_calls = model(monkeypatch, fail_episode='E02.')
    payload = choices(client, job_id)
    first = wait_operation(client, start_ai(client, job_id, payload)['operation_id'])
    assert first['status'] == 'failed' and first['result']['failed_count'] == 1
    assert first['result']['file_count'] == 2
    assert client.get(f'/api/tasks/{job_id}/ai-sync/download').status_code == 200
    second_calls = model(monkeypatch)
    assert client.put('/api/settings/ai', json={'timeout_seconds': 300}).status_code == 200
    second = wait_operation(client, start_ai(client, job_id, payload)['operation_id'])
    assert second['status'] == 'completed'
    assert second['result']['reused_count'] == 2
    assert len(second_calls) == 1 and 'E02.' in second_calls[0]
    assert len(first_calls) == 3
    assert second['result']['filename'] != first['result']['filename']
    assert len(client.get('/api/archive').json()['titles'][0]['subtitles']) == 2


def test_duplicate_submission_and_reserved_parent_block_rebuild_and_validate_queue_snapshot(client, settings, monkeypatch):
    directory = season(settings, 2)
    configure(monkeypatch)
    job_id = submit(client, directory)
    wait_batch(client, job_id)
    release, started = threading.Event(), threading.Event()
    calls = []
    async def blocked(settings, instruction, data):
        if data.get('test') == 'connection':
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return {'ok': True}, .1, None
        calls.append(data)
        raise AssertionError('Changed queued workpack must not reach the model')
    monkeypatch.setattr('app.api.ai_sync.chat_request', blocked)
    blocker = client.post('/api/settings/ai/test').json()
    assert started.wait(2)
    try:
        payload = choices(client, job_id)
        submitted = start_ai(client, job_id, payload)
        assert submitted['status'] == 'queued'
        assert start_ai(client, job_id, payload)['operation_id'] == submitted['operation_id']
        assert client.post(f'/api/tasks/{job_id}/batch', json={'action': 'resume'}).status_code == 409
        parent_dir = settings.data_root / 'work' / 'jobs' / job_id
        data = json.loads((parent_dir / 'manifest.json').read_text())
        data['changed'] = True
        (parent_dir / 'manifest.json').write_text(json.dumps(data))
    finally:
        release.set()
    wait_operation(client, blocker['operation_id'])
    state = wait_operation(client, submitted['operation_id'])
    assert state['status'] == 'failed' and state['http_status'] == 409
    assert calls == []


def test_restart_keeps_completed_ai_results_and_requires_explicit_resubmission(settings, monkeypatch):
    directory = season(settings, 3)
    configure(monkeypatch)
    monkeypatch.setattr('app.main.probe_tools', lambda: ToolInfo('ffmpeg', 'ffprobe', 'mkvextract'))
    calls, blocked = [], threading.Event()
    block = True
    model(monkeypatch, calls=calls)
    from app.api import ai_sync
    reply = ai_sync.chat_request
    async def interrupted(settings, instruction, data):
        reference = data['english'][0]['text']
        if block and 'E02.' in reference:
            blocked.set()
            await asyncio.Event().wait()
        return await reply(settings, instruction, data)
    monkeypatch.setattr(ai_sync, 'chat_request', interrupted)
    with TestClient(create_app(settings)) as first:
        authenticate(first)
        job_id = submit(first, directory)
        wait_batch(first, job_id)
        payload = choices(first, job_id)
        operation = start_ai(first, job_id, payload)
        assert blocked.wait(3)
    block = False
    with TestClient(create_app(settings)) as second:
        authenticate(second)
        state = second.get(f'/api/tasks/{job_id}/ai-status').json()
        assert state['status'] == 'failed'
        assert len(calls) == 1
        resumed = wait_operation(second, start_ai(second, job_id, choices(second, job_id))['operation_id'])
        assert resumed['status'] == 'completed'
        assert resumed['result']['reused_count'] == 1
        assert len(calls) == 3
        assert second.get(f'/api/tasks/{job_id}/ai-sync/download').status_code == 200


def test_result_integrity_and_model_changes_invalidate_only_the_right_cache(client, settings, monkeypatch):
    directory = season(settings, 2)
    configure(monkeypatch)
    job_id = submit(client, directory)
    wait_batch(client, job_id)
    calls = model(monkeypatch)
    payload = choices(client, job_id)
    first = wait_operation(client, start_ai(client, job_id, payload)['operation_id'])
    assert first['status'] == 'completed'
    assert client.put('/api/settings/ai', json={'model': 'different-model'}).status_code == 200
    second = wait_operation(client, start_ai(client, job_id, payload)['operation_id'])
    assert second['status'] == 'completed' and second['result']['reused_count'] == 0
    assert len(calls) == 4
    parent = settings.data_root / 'work' / 'jobs' / job_id
    pointer = parent / 'ai-sync-batch.json'
    metadata = json.loads(pointer.read_text())
    metadata['relative_path'] = '../../outside.zip'
    pointer.write_text(json.dumps(metadata))
    assert client.get(f'/api/tasks/{job_id}/ai-sync/download').status_code == 410


def test_changing_one_polish_choice_reuses_other_episodes(client, settings, monkeypatch):
    directory = season(settings, 2)
    extra = directory / 'Example.Show.S01E01.WEB.alt.pl.srt'
    extra.write_text('1\n00:00:01,000 --> 00:00:02,000\nAlternatywny tekst odcinka 1.\n')
    configure(monkeypatch)
    job_id = submit(client, directory)
    wait_batch(client, job_id)
    calls = model(monkeypatch)
    payload = choices(client, job_id)
    assert wait_operation(client, start_ai(client, job_id, payload)['operation_id'])['status'] == 'completed'
    inputs = client.get(f'/api/tasks/{job_id}/ai-inputs').json()['items']
    changed = next(row for row in inputs if len(row['polish_candidates']) == 2)
    for row in payload['items']:
        if row['job_id'] == changed['job_id']:
            row['polish_file'] = changed['polish_candidates'][1]['archiveName']
    retried = wait_operation(client, start_ai(client, job_id, payload)['operation_id'])
    assert retried['status'] == 'completed'
    assert retried['result']['reused_count'] == 1
    assert len(calls) == 3


def test_long_episode_names_keep_srt_extensions_in_results_zip(client, settings, monkeypatch):
    directory = season(settings, 1)
    old = directory / 'Example.Show.S01E01.WEB'
    name = 'Example' + 'x' * 170 + '.S01E01.WEB'
    old.with_suffix('.WEB.mkv').rename(directory / (name + '.mkv'))
    (directory / 'Example.Show.S01E01.WEB.pl.srt').rename(directory / (name + '.pl.srt'))
    configure(monkeypatch)
    job_id = submit(client, directory)
    assert wait_batch(client, job_id)['status'] == 'WORKPACK_READY'
    model(monkeypatch)
    state = wait_operation(client, start_ai(client, job_id, choices(client, job_id))['operation_id'])
    assert state['status'] == 'completed'
    with zipfile.ZipFile(io.BytesIO(client.get(f'/api/tasks/{job_id}/ai-sync/download').content)) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        assert manifest['items'][0]['output']['name'].endswith('.pl.srt')
