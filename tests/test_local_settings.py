import httpx
import pytest

from app.services.job_manager import JobManager
from app.services.local_semantic import EmbeddingClient, LocalProtocolError, MODEL, REVISION
from pydantic import SecretStr


def test_settings_page_and_persistence_without_secret(client,settings):
    settings.local_worker_token=SecretStr('DO-NOT-RETURN-THIS')
    assert client.get('/settings').status_code==200
    data={'mode':'lan_gpu','lanWorkerUrl':'http://gpu-worker:8091','cpuFallbackEnabled':False}
    response=client.put('/api/local-settings',json=data)
    assert response.status_code==200 and response.json()['mode']=='lan_gpu'
    assert 'DO-NOT-RETURN-THIS' not in response.text
    assert settings.local_worker_url=='http://gpu-worker:8091' and settings.local_worker_expected_device=='cuda'
    fresh=settings.model_copy(update={'local_worker_url':None})
    restarted=JobManager(settings.data_root/'subtitle-agent.db',fresh)
    assert fresh.local_worker_url=='http://gpu-worker:8091'
    assert client.put('/api/local-settings',json={'mode':'cpu'}).status_code==200
    assert settings.local_worker_url=='http://subtitle-semantic-worker:8091'
    assert client.put('/api/local-settings',json={'mode':'disabled'}).status_code==200
    assert settings.local_worker_url is None
    assert client.put('/api/local-settings',json={'mode':'lan_gpu'}).status_code==422
    assert client.put('/api/local-settings',json=dict(data,lanWorkerUrl='http://user:password@worker')).status_code==422


@pytest.mark.anyio
async def test_device_choice_must_match_worker(settings):
    settings.local_worker_expected_device='cuda'
    metadata={'model':MODEL,'revision':REVISION,'device':'cpu','dtype':'float32','dimension':768,'maxTokens':128}
    client=EmbeddingClient(settings,httpx.MockTransport(lambda request:httpx.Response(200,json=metadata)),url='http://worker')
    with pytest.raises(LocalProtocolError,match='Urządzenie'):await client.ready()
