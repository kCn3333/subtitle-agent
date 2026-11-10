import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from app.services.ai_sync import ApiSettings, ApiSettingsStore, ModelConfiguration, ModelLimitError
from tests.conftest import complete_ai


def configuration(name='one', key=None, **values):
    settings = {'api_url': 'http://model/v1', 'model': name, **values}
    if key is not None:
        settings['api_key'] = key
    return {'settings': settings}


def test_saved_models_crud_capacity_secrets_and_limit_changes(client):
    listed = client.get('/api/settings/ai/models').json()
    assert listed == {'models': [], 'active_id': None, 'limit': 10}
    first = None
    for number in range(10):
        response = client.post('/api/settings/ai/models', json=configuration(str(number), f'private-{number}'))
        assert response.status_code == 200
        assert f'private-{number}' not in response.text
        first = first or response.json()['id']
    assert client.post('/api/settings/ai/models', json=configuration('over-limit')).status_code == 409
    edited = configuration('0', timeout_seconds=300)
    edited.update(model_id=first, key_source_id=first)
    assert client.post('/api/settings/ai/models', json=edited).status_code == 200
    selected = client.post(f'/api/settings/ai/models/{first}/activate')
    assert selected.json()['timeout_seconds'] == 300 and selected.json()['api_key_set']
    assert client.app.state.ai_settings.get().api_key.get_secret_value() == 'private-0'
    assert 'private-' not in client.get('/api/settings/ai/models').text
    assert client.get('/api/settings/ai/models').json()['active_id'] == first
    settings = client.get('/api/settings/app').json()
    settings['ai_max_saved_models'] = 9
    assert client.put('/api/settings/app', json=settings).status_code == 422
    assert client.get('/api/settings/app').json()['ai_max_saved_models'] == 10
    assert client.delete(f'/api/settings/ai/models/{first}').status_code == 200
    assert client.delete(f'/api/settings/ai/models/{first}').status_code == 404
    assert client.put('/api/settings/app', json=settings).status_code == 200
    assert client.get('/api/settings/ai/models').json()['limit'] == 9
    assert client.post('/api/settings/ai/models', json=configuration('over-limit')).status_code == 409
    settings['ai_max_saved_models'] = 11
    assert client.put('/api/settings/app', json=settings).status_code == 200
    assert client.post('/api/settings/ai/models', json=configuration('over-limit')).status_code == 200


def test_temporary_test_and_use_do_not_save_or_replace_selected_model(client, prepared_job, monkeypatch, settings):
    client.put('/api/settings/ai', json=configuration('persisted', 'old-key')['settings'])
    calls = []

    async def reply(config, instruction, data):
        calls.append((config.model, config.api_key.get_secret_value()))
        result = {'ok': True} if 'test' in data else {'segments': [
            {'id': cue['id'], 'start_ms': 3000, 'end_ms': 4000} for cue in data['polish']]}
        return result, .1, {'total_tokens': 1}

    monkeypatch.setattr('app.api.ai_sync.chat_request', reply)
    draft = configuration('temporary', 'draft-key')
    assert complete_ai(client, '/api/settings/ai/test', json=draft).status_code == 200
    assert client.get('/api/settings/ai').json()['model'] == 'persisted'
    assert client.post('/api/settings/ai/activate', json=draft).status_code == 200
    assert client.get('/api/settings/ai').json()['model'] == 'temporary'
    job_id, _ = prepared_job
    assert complete_ai(client, f'/api/tasks/{job_id}/ai-sync', json={
        'polish_file': 'polish/original.pl.srt', 'reference_source_id': 'embedded:4'}).status_code == 200
    assert calls == [('temporary', 'draft-key'), ('temporary', 'draft-key')]
    assert client.get('/api/settings/ai/models').json()['models'] == []
    with sqlite3.connect(settings.data_root / 'subtitle-agent.db') as db:
        assert 'draft-key' not in db.execute('SELECT data FROM ai_api_settings').fetchone()[0]
        assert 'draft-key' not in ''.join(row[0] for row in db.execute('SELECT data FROM ai_operations'))
    restarted = ApiSettingsStore(settings.data_root / 'subtitle-agent.db')
    assert restarted.get().model == 'persisted'
    assert restarted.get().api_key.get_secret_value() == 'old-key'


def test_profile_keys_are_scoped_to_source_and_api_endpoint(client):
    first = client.post('/api/settings/ai/models', json=configuration('with-key', 'first-key')).json()['id']
    second = client.post('/api/settings/ai/models', json=configuration('without-key')).json()['id']
    assert client.post(f'/api/settings/ai/models/{first}/activate').json()['api_key_set']
    assert not client.post(f'/api/settings/ai/models/{second}/activate').json()['api_key_set']
    assert client.get('/api/settings/ai/models').json()['active_id'] == second
    draft = configuration('draft')
    draft['key_source_id'] = first
    assert client.post('/api/settings/ai/activate', json=draft).json()['api_key_set']
    assert client.app.state.ai_settings.get().api_key.get_secret_value() == 'first-key'
    draft['settings']['api_url'] = 'http://different-server/v1'
    assert not client.post('/api/settings/ai/activate', json=draft).json()['api_key_set']
    draft['settings']['api_url'] = 'http://model/v1'
    draft['settings']['api_key'] = ''
    assert not client.post('/api/settings/ai/activate', json=draft).json()['api_key_set']
    draft['key_source_id'] = 'missing'
    assert client.post('/api/settings/ai/activate', json=draft).status_code == 404


def test_migration_seeds_existing_config_once_and_saved_models_survive_restart(tmp_path):
    path = tmp_path / 'models.db'
    config = ApiSettings(api_url='http://local/v1', model='existing', api_key='stored-key')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE ai_api_settings(id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('INSERT INTO ai_api_settings VALUES(1,?)', (json.dumps({**config.model_dump(exclude={'api_key'}), 'api_key': 'stored-key'}),))
    store = ApiSettingsStore(path)
    model_id = store.models()['models'][0]['id']
    restarted = ApiSettingsStore(path)
    assert restarted.model(model_id) == config
    assert len(restarted.models()['models']) == 1
    restarted.delete_model(model_id)
    assert ApiSettingsStore(path).models()['models'] == []


def test_model_capacity_is_atomic_across_parallel_writes(tmp_path):
    store = ApiSettingsStore(tmp_path / 'models.db')

    def save(name):
        try:
            store.save_model(ModelConfiguration.model_validate(configuration(name)), 1)
            return True
        except ModelLimitError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ['a', 'b'])) == [False, True]
    assert len(store.models()['models']) == 1


def test_models_require_session_and_csrf(client):
    assert client.post('/api/settings/ai/models', json=configuration(), headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    client.cookies.clear()
    assert client.get('/api/settings/ai/models').status_code == 401
    assert client.post('/api/settings/ai/activate', json=configuration()).status_code == 401
