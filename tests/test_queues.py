import asyncio
import json
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest

from app.models.job import JobStatus, WorkpackTaskType
from app.services.job_manager import JobManager
from app.services.operation_queue import AiQueue, JobBusy, QueueBusy


from tests.helpers import wait_operation


def clone_job(client, settings, source):
    old, directory=source
    job=str(uuid.uuid4())
    import shutil
    shutil.copytree(directory, directory.parent/job)
    with sqlite3.connect(settings.data_root/'subtitle-agent.db') as db:
        db.row_factory=sqlite3.Row
        row=dict(db.execute('SELECT * FROM jobs WHERE id=?',(old,)).fetchone())
        row.update(id=job, media_path=f'/media/{job}.mkv')
        columns=list(row)
        db.execute(f"INSERT INTO jobs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",list(row.values()))
    return job


def test_ai_queue_serializes_jobs_and_tests_returns_202_and_releases_after_failure(client,prepared_job,settings,monkeypatch):
    job,_=prepared_job
    second=clone_job(client,settings,prepared_job)
    started,release=threading.Event(),threading.Event()
    calls=[]
    async def fake(config,instruction,data):
        calls.append(config.model)
        if len(calls)==1:
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            from app.services.ai_sync import AiSyncError
            raise AiSyncError('First operation failed')
        if 'test' in data:
            return {'ok':True},.1,None
        return {'segments':[{'id':'pl:1','start_ms':3000,'end_ms':4500}]},.1,None
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    client.put('/api/settings/ai',json={'api_url':'http://model/v1','model':'first','api_key':'queue-secret-not-in-status'})
    payload={'reference_source_id':'embedded:4','polish_file':'polish/original.pl.srt'}
    first=client.post(f'/api/tasks/{job}/ai-sync',json=payload)
    assert first.status_code==202
    assert started.wait(3)
    try:
        duplicate=client.post(f'/api/tasks/{job}/ai-sync',json=payload)
        assert duplicate.status_code==202
        assert duplicate.json()['operation_id']==first.json()['operation_id']
        assert client.post(f'/api/tasks/{job}/ai-sync',json={**payload,'polish_file':'other.srt'}).status_code==409
        queued=client.post(f'/api/tasks/{second}/ai-sync',json=payload)
        assert queued.status_code==202
        state=client.get(f'/api/tasks/{second}/ai-status').json()
        assert state['status']=='queued' and state['queue_position']==1
        assert second in client.app.state.jobs.artifact_users
        # This must fail because the AI reservation is held, before attempting a rebuild.
        assert client.post(f'/api/workpacks/{second}/reference',json={'referenceSourceId':'embedded:4'}).status_code==409
        test=client.post('/api/settings/ai/test')
        assert test.status_code==202 and test.json()['queue_position']==2
        assert len(calls)==1
        assert 'queue-secret-not-in-status' not in json.dumps(state)
        assert client.get('/health').status_code==200
        client.put('/api/settings/ai',json={'api_url':'http://model/v1','model':'changed'})
    finally:
        release.set()
    assert wait_operation(client,first.json()['operation_id'])['status']=='failed'
    assert wait_operation(client,queued.json()['operation_id'])['status']=='completed'
    assert wait_operation(client,test.json()['operation_id'])['status']=='completed'
    assert calls==['first','first','first']  # Settings are captured at admission.
    assert not client.app.state.jobs.reserved_jobs
    assert not client.app.state.jobs.artifact_users


def test_ai_queue_full_is_bounded_and_does_not_reserve_rejected_job(client,prepared_job,settings,monkeypatch):
    started,release=threading.Event(),threading.Event()
    async def fake(*args):
        started.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return {'ok':True},.1,None
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    active=client.post('/api/settings/ai/test')
    assert active.status_code==202 and started.wait(3)
    queued=[]
    try:
        for _ in range(settings.max_queued_ai_operations):
            response=client.post('/api/settings/ai/test')
            assert response.status_code==202
            queued.append(response.json()['operation_id'])
        job,_=prepared_job
        response=client.post(f'/api/tasks/{job}/ai-sync',json={'reference_source_id':'embedded:4','polish_file':'polish/original.pl.srt'})
        assert response.status_code==429
        assert response.json()['detail']['code']=='QUEUE_FULL'
        assert job not in client.app.state.jobs.reserved_jobs
        assert job not in client.app.state.jobs.artifact_users
    finally:
        release.set()
    for operation_id in [active.json()['operation_id'],*queued]:
        assert wait_operation(client,operation_id)['status']=='completed'


@pytest.mark.anyio
async def test_rebuild_uses_local_queue_deduplicates_and_bounds_admission(settings,monkeypatch):
    settings.max_queued_jobs=1
    settings.data_root.mkdir(parents=True,exist_ok=True)
    manager=JobManager(settings.data_root/'jobs.db',settings)
    started,release=asyncio.Event(),asyncio.Event()
    calls=[]
    reference={'streamIndex':4,'codec':'subrip','language':'eng','type':'text','sourceType':'embedded'}
    report={'media':{'embeddedSubtitles':[reference]},'externalSubtitles':[], 'pipeline':'PREPARE_SYNC'}
    old=str(uuid.uuid4())
    with manager._connect() as db:
        db.execute('INSERT INTO jobs(id,media_path,status,progress,created_at,job_type,task_type,report_json) VALUES (?,?,?,?,?,?,?,?)',
                   (old,'old.mkv','WORKPACK_READY',100,'old','PREPARE_WORKPACK','PREPARE_SYNC',json.dumps(report)))
    class Pipeline:
        async def prepare(self, manager, job_id, cached=None, selected=None):
            calls.append((job_id,selected))
            started.set()
            await release.wait()
            await manager._emit(job_id,'SUCCESS',JobStatus.WORKPACK_READY,'Done',100)
    monkeypatch.setattr('app.services.job_manager.workpack_service',lambda _:Pipeline())
    await manager.start()
    try:
        first=await manager.create_workpack('first.mkv',WorkpackTaskType.PREPARE_SYNC)
        await asyncio.wait_for(started.wait(),3)
        after=await manager.rebuild_workpack(old,'embedded:4')
        assert await manager.rebuild_workpack(old,'embedded:4')==after
        assert manager._queue.qsize()==1
        assert old in manager.artifact_users
        with pytest.raises(JobBusy):
            manager.reserve_job(old,'ai')
        assert (await manager.create_workpack('first.mkv',WorkpackTaskType.PREPARE_SYNC))['id']==first['id']
        before=len(manager.list_jobs())
        with pytest.raises(QueueBusy):
            await manager.create_workpack('overflow.mkv',WorkpackTaskType.PREPARE_SYNC)
        assert len(manager.list_jobs())==before
        assert calls==[(first['id'],None)]
        release.set()
        await asyncio.wait_for(manager._queue.join(),3)
        assert calls==[(first['id'],None),(old,'embedded:4')]
        assert not manager.reserved_jobs and not manager.artifact_users
    finally:
        release.set()
        await manager.close()


@pytest.mark.anyio
async def test_ai_restart_marks_unfinished_operations_interrupted_without_replaying(settings):
    settings.data_root.mkdir(parents=True,exist_ok=True)
    manager=JobManager(settings.data_root/'jobs.db',settings)
    path=manager.db_path
    state={'operation_id':'interrupted-op','status':'queued','mode':'test','phase':'Waiting'}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE ai_operations (id TEXT PRIMARY KEY, job_id TEXT, data TEXT NOT NULL)')
        db.execute('INSERT INTO ai_operations VALUES (?,?,?)',('interrupted-op',None,json.dumps(state)))
    queue=AiQueue(path,manager,{})
    assert queue.status('interrupted-op')['status']=='failed'
    assert queue.status('interrupted-op')['error']['code']=='INTERRUPTED'
    assert queue.queue.empty()
    queue.start()
    await asyncio.sleep(0)
    assert queue.queue.empty()
    await queue.close()


@pytest.mark.anyio
async def test_ai_shutdown_releases_running_and_waiting_reservations(settings):
    settings.data_root.mkdir(parents=True,exist_ok=True)
    class Jobs:
        def __init__(self):self.pins=set()
        def reserve_job(self,job,purpose):self.pins.add(job)
        def release_job(self,job):self.pins.remove(job)
        def cleanup_expired_artifacts(self):pass
    jobs=Jobs()
    queue=AiQueue(settings.data_root/'jobs.db',jobs,{})
    started=asyncio.Event()
    async def run():
        started.set()
        await asyncio.Event().wait()
    first=queue.submit({'job_id':'first'},run)
    queue.start()
    await started.wait()
    second=queue.submit({'job_id':'second'},run)
    await queue.close()
    assert not jobs.pins
    assert queue.status(first['operation_id'])['status']=='failed'
    assert queue.status(second['operation_id'])['status']=='failed'
    assert queue.queue.empty()


def test_waiting_ai_input_files_are_protected_from_retention(client,prepared_job,settings,monkeypatch):
    started,release=threading.Event(),threading.Event()
    async def fake(config,instruction,data):
        if 'test' in data:
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return {'ok':True},.1,None
        return {'segments':[{'id':'pl:1','start_ms':3000,'end_ms':4500}]},.1,None
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    active=client.post('/api/settings/ai/test')
    assert started.wait(3)
    job,directory=prepared_job
    queued=client.post(f'/api/tasks/{job}/ai-sync',json={'reference_source_id':'embedded:4','polish_file':'polish/original.pl.srt'})
    assert queued.status_code==202
    try:
        manager=client.app.state.jobs
        with manager._connect() as db:
            for index in range(30):
                db.execute('INSERT INTO jobs(id,media_path,status,progress,created_at,job_type,task_type,report_json) VALUES (?,?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()),f'/media/newer-{index}.mkv','WORKPACK_READY',100,f'2099-01-01T00:00:{index:02d}',
                            'PREPARE_WORKPACK','PREPARE_SYNC','{}'))
        manager.cleanup_expired_artifacts()
        assert manager.get(job) is not None
        assert directory.is_dir() and (directory/'manifest.json').is_file()
    finally:
        release.set()
    assert wait_operation(client,active.json()['operation_id'])['status']=='completed'
    assert wait_operation(client,queued.json()['operation_id'])['status']=='completed'


@pytest.mark.anyio
async def test_timeout_and_cleanup_failure_do_not_stall_ai_worker(settings):
    settings.data_root.mkdir(parents=True,exist_ok=True)
    class Jobs:
        def __init__(self):self.pins=set()
        def reserve_job(self,job,purpose):self.pins.add(job)
        def release_job(self,job):self.pins.remove(job)
        def cleanup_expired_artifacts(self):raise OSError('simulated cleanup failure')
    jobs=Jobs()
    queue=AiQueue(settings.data_root/'jobs.db',jobs,{})
    async def timeout():
        async with asyncio.timeout(.01):
            await asyncio.Event().wait()
    async def success():return {'ok':True}
    first=queue.submit({'job_id':'timeout'},timeout)
    second=queue.submit({'job_id':'next'},success)
    queue.start()
    try:
        await asyncio.wait_for(queue.queue.join(),3)
        assert queue.status(first['operation_id'])['status']=='failed'
        assert queue.status(second['operation_id'])['status']=='completed'
        assert not jobs.pins
    finally:
        await queue.close()
