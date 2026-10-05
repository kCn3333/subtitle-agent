import asyncio

import httpx
import pytest

from app.core.config import Settings
from app.models.job import WorkpackTaskType
from app.services.ai_health import ApiHealth
from app.services.ai_sync import ApiSettings
from app.services.job_manager import JobManager
from app.services.media_title import display_title


@pytest.mark.parametrize('filename,title', [
    ('Come and See (1985) [Bluray-1080p][x264][GROUP].mkv', 'Come and See (1985)'),
    ('Serial.S01E02.1080p.WEB-DL.mkv', 'Serial · S01E02'),
    ('Serial.S02E03-E04.720p.mkv', 'Serial · S02E03–E04'),
    ('Serial.1x02.mkv', 'Serial · S01E02'),
    ('Zażółć.gęślą.mkv', 'Zażółć gęślą'),
])
def test_readable_title(filename, title):
    assert display_title('/private/media/'+filename) == title


@pytest.mark.anyio
async def test_new_operations_start_with_action_and_title(tmp_path):
    manager = JobManager(tmp_path/'jobs.db', Settings(data_root=tmp_path))
    for mode in WorkpackTaskType:
        job = await manager.create_workpack('/private/Movie.1985.1080p.mkv', mode)
        first = manager.events(job['id'])[0]
        assert first.message.endswith(' · Film: Movie (1985)')
        assert '1080p' not in first.message and '/private' not in first.message
        assert job['display_title'] == 'Movie (1985)'


@pytest.mark.anyio
async def test_health_cached_without_model_inference_and_invalidates_on_settings_change():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/v1/models'
        assert request.headers['Authorization'].startswith('Bearer ')
        return httpx.Response(200)
    transport = httpx.MockTransport(handler)
    health = ApiHealth()
    settings = ApiSettings(api_url='http://model/v1/chat/completions', model='test', api_key='secret')
    results = await asyncio.gather(*(health.check(settings, transport) for _ in range(3)))
    assert all(result['available'] for result in results)
    assert len(calls) == 1
    assert 'secret' not in str(results)
    await health.check(settings.model_copy(update={'model':'other'}), transport)
    assert len(calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('status,available', [(200, True), (401, False), (403, False), (500, False)])
async def test_health_status(status, available):
    result = await ApiHealth().check(ApiSettings(api_url='http://model/v1', model='test'),
                                    httpx.MockTransport(lambda _:httpx.Response(status)))
    assert result['available'] is available


@pytest.mark.anyio
async def test_health_fallback_and_network_failure():
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(404 if request.method == 'GET' else 405)
    settings = ApiSettings(api_url='http://model/v1', model='test')
    assert (await ApiHealth().check(settings, httpx.MockTransport(handler)))['available']
    assert calls == [('GET','/v1/models'), ('HEAD','/v1/chat/completions')]
    def disconnected(request):
        raise httpx.ConnectError('private diagnostic', request=request)
    result = await ApiHealth().check(settings, httpx.MockTransport(disconnected))
    assert not result['available'] and 'private diagnostic' not in str(result)
    assert not (await ApiHealth().check(ApiSettings()))['configured']


def test_health_route_unconfigured_and_badge_on_all_pages(client):
    response = client.get('/api/settings/ai/health')
    assert response.status_code == 200
    assert response.json()['configured'] is False
    for page in ('/', '/settings', '/archive'):
        html = client.get(page).text
        assert 'id="api-health"' in html
        assert '/static/header.js' in html
