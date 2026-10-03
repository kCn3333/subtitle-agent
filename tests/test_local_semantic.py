import asyncio
import time
from dataclasses import replace

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.models.job import AlignmentMode, JobStatus
from app.services.alignment import Anchor, Cue, coverage_report, fit_models, select_model, transform
from app.services.local_semantic import (EmbeddingClient, LocalProtocolError, LocalUnavailable, MODEL, REVISION,
                                        groups, match_groups, monotonic_chain, synchronize)
from app.services.prepared_reference import PreparedReference
from app.services.media_analysis import UserInputError
from app.services.job_manager import JobManager
from semantic_worker.main import create_app


def meta():
    return {'model': MODEL, 'revision': REVISION, 'device': 'cpu', 'dtype': 'float32',
            'dimension': 768, 'maxTokens': 128, 'inferenceMs': 1}


def cue(i, text=None, offset=0):
    return Cue(str(i),i+1,i*10000+offset,i*10000+1000+offset,1000,text or f'unique sentence number {i}',
               text or f'unique sentence number {i}','test')


@pytest.mark.anyio
@pytest.mark.parametrize('kind',['missing','duplicate','foreign','dimension','nan','inf','revision'])
async def test_worker_response_rejected(settings,kind):
    def handler(request):
        vector=[0.0]*768;vector[0]=1
        data={'embeddings':[{'id':'a','vector':vector}], 'metadata':meta()}
        if kind=='missing':data['embeddings']=[]
        if kind=='duplicate':data['embeddings']*=2
        if kind=='foreign':data['embeddings'][0]['id']='x'
        if kind=='dimension':data['embeddings'][0]['vector']=[1]
        if kind in {'nan','inf'}:
            # Valid JSON numerical overflow becomes infinity in the decoder.
            raw='{"embeddings":[{"id":"a","vector":['+','.join([('NaN' if kind=='nan' else '1e999') if i==0 else '0' for i in range(768)])+']}],"metadata":'+__import__('json').dumps(meta())+'}'
            return httpx.Response(200,content=raw)
        if kind=='revision':data['metadata']['revision']='main'
        return httpx.Response(200,json=data)
    client=EmbeddingClient(settings,httpx.MockTransport(handler),url='http://worker')
    with pytest.raises(LocalProtocolError):await client.embed([{'id':'a','text':'text'}])


@pytest.mark.anyio
async def test_retry_timeout_and_id_mapping(settings):
    settings.local_worker_retries=1; calls=[]
    def handler(request):
        calls.append(1)
        if len(calls)==1:raise httpx.ReadTimeout('test',request=request)
        return httpx.Response(200,json={'embeddings':[{'id':id,'vector':[1]+[0]*767} for id in ['b','a']], 'metadata':meta()})
    result=await EmbeddingClient(settings,httpx.MockTransport(handler),url='http://worker').embed([{'id':id,'text':'text'} for id in ['a','b']])
    assert set(result)=={'a','b'} and len(calls)==2
    with pytest.raises(LocalUnavailable):
        await EmbeddingClient(settings,httpx.MockTransport(lambda r:httpx.Response(503)),url='http://worker').ready()


def test_weights_and_clamp_regressions():
    anchors=[Anchor(i,i,i*10000+2000,i*10000,.9,'local') for i in range(8)]
    anchors += [Anchor(i+8,i+8,i*10000+12000,i*10000,.001,'local') for i in range(30)]
    assert select_model(fit_models(anchors))['offsetMs']==2000
    transformed,report=transform([cue(1)],{'predict':lambda t:t+1000},10000,0)
    assert transformed[0].start_ms==11000 and transformed[0].end_ms==10000
    assert report['reversedSegments']==1 and report['clampedSegments']==1
    assert report['textPreserved'] and report['segmentCountPreserved']


def test_coverage_beginning_is_not_full():
    en=[cue(i) for i in range(100)]
    anchors=[Anchor(i,i,i*1000,i*1000,1,'local') for i in range(12)]
    report=coverage_report(anchors,en,1000000)
    assert report['materialCoverage']<.02 and report['dialogueCoverage']<=.1 and not report['end']


def test_sparse_dp_monotonic_groups_no_reuse():
    candidates=[{'enStart':0,'enEnd':1,'plStart':0,'plEnd':2,'weight':1},
                {'enStart':1,'enEnd':3,'plStart':2,'plEnd':3,'weight':1},
                {'enStart':1,'enEnd':2,'plStart':1,'plEnd':2,'weight':.5},
                {'enStart':3,'enEnd':4,'plStart':0,'plEnd':1,'weight':.2}]
    chain=monotonic_chain(candidates,4)
    assert len(chain)==2
    assert chain[0]['enEnd']<=chain[1]['enStart'] and chain[0]['plEnd']<=chain[1]['plStart']


def test_group_content_search_handles_large_offset_and_foreign_film():
    en,pl=[cue(i) for i in range(12)],[cue(i,offset=500000) for i in range(12)]
    eg,pg=groups(en,'en'),groups(pl,'pl');vectors={}
    for g in eg+pg:
        v=np.zeros(768,dtype=np.float32);v[g.start:g.end]=1;vectors[g.id]=v/np.linalg.norm(v)
    relations=match_groups(en,pl,eg,pg,vectors)
    assert len(relations)==12
    for g in pg:
        v=np.zeros(768,dtype=np.float32);v[100+g.start:100+g.end]=1;vectors[g.id]=v/np.linalg.norm(v)
    assert match_groups(en,pl,eg,pg,vectors)==[]


def test_prepared_ocr_source_switch_and_hash(tmp_path):
    path=tmp_path/'ocr.srt';path.write_text('content')
    source={'sourceType':'embedded','streamIndex':4,'type':'graphic'}
    prepared=PreparedReference.create(source,path,{'structuralQuality':'GOOD','textQuality':'SUSPECT'})
    assert PreparedReference.verified(prepared.to_dict(),source).provenance=='ocr'
    with pytest.raises(UserInputError):PreparedReference.verified(prepared.to_dict(),dict(source,streamIndex=5))
    path.write_text('changed')
    with pytest.raises(UserInputError):PreparedReference.verified(prepared.to_dict(),source)
    bad=PreparedReference.create(source,path,{'structuralQuality':'BAD'})
    with pytest.raises(UserInputError):PreparedReference.verified(bad.to_dict(),source)


class FakeEncoder:
    def metadata(self): return meta()
    def encode(self,items):
        time.sleep(.08)
        return {'embeddings':[{'id':x['id'],'vector':[1]+[0]*767} for x in items],'metadata':meta()}


def test_worker_auth_limits_health_busy_and_no_model_download(monkeypatch):
    monkeypatch.setenv('AI_TOKEN','test-token')
    with TestClient(create_app(FakeEncoder)) as client:
        for _ in range(20):
            response=client.get('/ready',headers={'Authorization':'Bearer test-token'})
            if response.status_code==200:break
            time.sleep(.01)
        assert response.status_code==200
        assert client.get('/health').status_code==200
        assert client.post('/embeddings',json={'segments':[{'id':'a','text':'x'}]}).status_code==401
        headers={'Authorization':'Bearer test-token'}
        assert client.post('/embeddings',headers=headers,json={'segments':[{'id':'a','text':'x'}]*2}).status_code==422
        assert client.post('/embeddings',headers=headers,content=b'x'*1048577).status_code==413
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            request=executor.submit(client.post,'/embeddings',headers=headers,json={'segments':[{'id':'a','text':'x'}]})
            time.sleep(.02)
            assert client.get('/health').status_code==200
            assert client.post('/embeddings',headers=headers,json={'segments':[{'id':'b','text':'y'}]}).status_code==429
            assert request.result().json()['embeddings'][0]['id']=='a'


@pytest.mark.anyio
async def test_queue_duplicate_wait_resume_cancel_restart_and_ocr_to_align(settings,media_file,monkeypatch):
    from test_jobs import _insert_analyzed_job
    settings.data_root.mkdir(parents=True);manager=JobManager(settings.data_root/'queue.db',settings)
    directory=settings.data_root/'work'/'jobs'/'local';directory.mkdir(parents=True)
    path=directory/'ref.srt';path.write_text('1\n00:00:01,000 --> 00:00:02,000\nEnglish reference text.\n')
    pl=media_file.parent/'test.pl.srt';pl.write_text('1\n00:00:01,000 --> 00:00:02,000\nPolski tekst oryginalny.\n')
    en_source={'sourceType':'embedded','streamIndex':1,'type':'graphic'}
    pl_source={'sourceType':'external','name':pl.name,'path':str(pl)}
    report={'preparedReference':PreparedReference.create(en_source,path,{'structuralQuality':'GOOD','textQuality':'GOOD'}).to_dict(),
            'englishRanking':[en_source],'polishRanking':[pl_source],'selectedEnglish':en_source,'selectedPolish':pl_source,
            'media':{'durationSeconds':10}}
    _insert_analyzed_job(manager,'local',report,media_file)
    await manager.start_alignment('local',None,None,AlignmentMode.LOCAL)
    with pytest.raises(UserInputError):await manager.start_alignment('local',None,None,AlignmentMode.LOCAL)
    await manager.start()
    await asyncio.wait_for(manager._queue.join(),2)
    assert manager.get('local')['status']=='WAITING_AI'
    await manager.close()
    restarted=JobManager(settings.data_root/'queue.db',settings)
    assert restarted.get('local')['status']=='WAITING_AI' and restarted.get('local')['pending_json']
    await restarted.cancel_alignment('local')
    assert restarted.get('local')['status']=='CANCELLED'
    assert not (directory/'preview.AI-Sync.pl.srt').exists()


def test_strict_parser_never_drops_malformed_polish(tmp_path):
    from app.services.alignment import parse_cues
    path=tmp_path/'broken.srt';path.write_text('1\n00:00:01,000 --> 00:00:02,000\nText\n\n2\nbroken time\nOther text\n')
    with pytest.raises(ValueError):parse_cues(path,'polish',True)


@pytest.mark.anyio
async def test_local_pipeline_preserves_text_and_independent_controls(settings):
    def handler(request):
        if request.url.path=='/ready':return httpx.Response(200,json=meta())
        items=__import__('json').loads(request.content)['segments'];results=[]
        for item in items:
            _,start,end=item['id'].split(':');v=[0.0]*768
            for i in range(int(start),int(end)):v[i]=1/(int(end)-int(start))**.5
            results.append({'id':item['id'],'vector':v})
        return httpx.Response(200,json={'embeddings':results,'metadata':meta()})
    client=EmbeddingClient(settings,httpx.MockTransport(handler),url='http://worker')
    original=[cue(i,offset=90000) for i in range(16)]
    after,report=await synchronize([cue(i) for i in range(16)],original,300000,client)
    assert [c.raw_text for c in original]==[c.raw_text for c in after]
    assert report['status']=='REVIEW_REQUIRED' and report['model']['offsetMs']==-90000
    assert report['controls']['independentOfFit'] and report['controlAnchorCount']==4
    assert max(report['controls']['residualMs'])==0


@pytest.mark.anyio
async def test_restart_invalidates_uncommitted_preview(settings,media_file):
    from test_jobs import _insert_analyzed_job
    settings.data_root.mkdir(parents=True);manager=JobManager(settings.data_root/'restart.db',settings)
    _insert_analyzed_job(manager,'interrupted',{'alignment':{'previewPath':'old'}},media_file)
    directory=settings.data_root/'work'/'jobs'/'interrupted';directory.mkdir(parents=True)
    (directory/'preview.AI-Sync.pl.srt').write_text('looks completed')
    with manager._connect() as db:
        db.execute('UPDATE jobs SET status=?, pending_json=? WHERE id=?',('TRANSFORMING_TIMESTAMPS','{"action":"alignment","mode":"LOCAL"}','interrupted'))
    restarted=JobManager(settings.data_root/'restart.db',settings)
    assert restarted.get('interrupted')['status']=='INTERRUPTED'
    assert 'alignment' not in restarted.get('interrupted')['report']
    assert not (directory/'preview.AI-Sync.pl.srt').exists()


def test_worker_timeout_holds_slot_until_real_computation_finishes(monkeypatch):
    monkeypatch.setenv('AI_TOKEN','test-token');monkeypatch.setenv('AI_REQUEST_TIMEOUT_SECONDS','.01')
    with TestClient(create_app(FakeEncoder)) as client:
        headers={'Authorization':'Bearer test-token'}
        for _ in range(20):
            if client.get('/ready',headers=headers).status_code==200:break
            time.sleep(.01)
        body={'segments':[{'id':'a','text':'some words'}]}
        assert client.post('/embeddings',headers=headers,json=body).status_code==504
        assert client.post('/embeddings',headers=headers,json=body).status_code==429
        assert client.get('/health').status_code==200
        time.sleep(.1)
        assert client.post('/embeddings',headers=headers,json=body).status_code==504


@pytest.mark.anyio
async def test_invalid_json_is_protocol_error(settings):
    client=EmbeddingClient(settings,httpx.MockTransport(lambda r:httpx.Response(200,content=b'not json')),url='http://worker')
    with pytest.raises(LocalProtocolError):await client.embed([{'id':'a','text':'text'}])


@pytest.mark.anyio
async def test_cancelled_queue_ticket_cannot_run_resubmitted_job_twice(settings,media_file,monkeypatch):
    from test_jobs import _insert_analyzed_job
    settings.data_root.mkdir(parents=True);manager=JobManager(settings.data_root/'tickets.db',settings)
    _insert_analyzed_job(manager,'ticket',{},media_file)
    calls=[]
    async def align(job_id,*args):
        calls.append(job_id)
        await manager._emit(job_id,'WARNING',JobStatus.REVIEW_REQUIRED,'done',100)
    monkeypatch.setattr(manager,'align',align)
    await manager.start_alignment('ticket',None,None,AlignmentMode.LOCAL)
    await manager.cancel_alignment('ticket')
    await manager.start_alignment('ticket',None,None,AlignmentMode.LOCAL)
    await manager.start()
    await asyncio.wait_for(manager._queue.join(),2)
    await manager.close()
    assert calls==['ticket']
