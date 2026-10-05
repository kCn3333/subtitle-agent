import asyncio
import threading

import pytest

from app.services.ai_sync import AiSyncError
from tests.test_ai_sync import prepared_job


@pytest.mark.anyio
@pytest.mark.parametrize('failed', [False, True])
async def test_server_reports_running_and_terminal_state_without_duplicate_request(client,prepared_job,monkeypatch,failed):
    job, _ = prepared_job
    started, release = threading.Event(), threading.Event()
    calls = []
    async def fake(settings,instruction,data):
        calls.append(data)
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        if failed:
            raise AiSyncError('Invalid model JSON', 2, {'total_tokens':42,'cost':0.01,'cost_currency':'USD'})
        return {'segments':[{'id':'pl:1','start_ms':3000,'end_ms':4500}]},2,{'total_tokens':42}
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    path=f'/api/tasks/{job}'
    payload={'polish_file':'polish/original.pl.srt','reference_source_id':'embedded:4'}
    assert client.get(path+'/ai-status').json() == {'status':'idle'}
    pending=asyncio.create_task(asyncio.to_thread(client.post,path+'/ai-sync',json=payload))
    try:
        assert await asyncio.to_thread(started.wait,3)
        state=client.get(path+'/ai-status').json()
        assert state['status']=='running'
        assert state['phase']=='Oczekiwanie na odpowiedź modelu'
        assert state['started_at'] and state['operation_id']
        assert state['polish_file']==payload['polish_file']
        rejected=client.post(path+'/ai-sync',json=payload)
        assert rejected.status_code==409
        assert rejected.json()['detail']['code']=='AI_OPERATION_RUNNING'
        assert client.get(path+'/ai-status').json()['operation_id']==state['operation_id']
        assert len(calls)==1
    finally:
        release.set()
        response=await pending
    assert response.status_code==(502 if failed else 200)
    terminal=client.get(path+'/ai-status').json()
    assert terminal['operation_id']==state['operation_id']
    assert terminal['status']==('failed' if failed else 'completed')
    metrics=terminal['error' if failed else 'result']
    assert metrics['usage']['total_tokens']==42
    assert not client.app.state.ai_sync_locks[job].locked()
    assert job not in client.app.state.jobs.artifact_users


def test_unknown_job_status_is_404(client):
    assert client.get('/api/tasks/unknown/ai-status').status_code==404
