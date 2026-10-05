import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import httpx
import pytest

from app.services.ai_sync import (AiSyncError, ApiSettings, ApiSettingsStore, chat_request,
                                  read_cues, segments, validate_result)
from app.services.alignment import sha256


def cue_file(path, text='  Zażółć <i>gęślą</i>\n- Druga kwestia.  '):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'7\n00:00:01,000 --> 00:00:02,000\n{text}\n', encoding='utf-8')
    return path


def test_preserves_text_and_reorders_response_by_id(tmp_path):
    path=cue_file(tmp_path/'pl.srt')
    with path.open('a') as handle:
        handle.write('\n7\n00:00:03,000 --> 00:00:04,000\nDruga.\n')
    cues=read_cues(path,'pl')
    assert [cue.cue_id for cue in cues]==['pl:1','pl:2']
    result=validate_result({'segments':[{'id':'pl:2','start_ms':5000,'end_ms':6000},
                                     {'id':'pl:1','start_ms':2000,'end_ms':3000}]},cues,10000)
    assert [cue.raw_text for cue in result]==[cue.raw_text for cue in cues]
    assert result[0].raw_text=='  Zażółć <i>gęślą</i>\n- Druga kwestia.  '
    assert result[0].start_ms==2000
    assert segments(cues)[0]['text']==cues[0].raw_text


@pytest.mark.parametrize('rows',[
    [],[{'id':'pl:1','start_ms':1,'end_ms':2}]*2,
    [{'id':'en:1','start_ms':1,'end_ms':2}],
    [{'id':'pl:1','start_ms':True,'end_ms':2}],
    [{'id':'pl:1','start_ms':1.5,'end_ms':2}],
    [{'id':'pl:1','start_ms':-1,'end_ms':2}],
    [{'id':'pl:1','start_ms':2,'end_ms':2}],
    [{'id':'pl:1','start_ms':3,'end_ms':2}],
    [{'id':'pl:1','start_ms':1,'end_ms':10001}],
    [{'id':'pl:1','start_ms':1,'end_ms':2,'text':'Replacement'}],
])
def test_rejects_invalid_complete_contract(tmp_path,rows):
    with pytest.raises(AiSyncError):
        validate_result({'segments':rows},read_cues(cue_file(tmp_path/'pl.srt'),'pl'),10000)


@pytest.mark.parametrize('content',['broken', '1\n00:99:01,000 --> 00:00:02,000\nHi',
                                     '1\n00:00:02,000 --> 00:00:01,000\nHi'])
def test_never_silently_drops_bad_input(tmp_path,content):
    path=tmp_path/'bad.srt';path.write_text(content)
    with pytest.raises(AiSyncError):read_cues(path,'pl')


def test_settings_persist_mask_and_clear_key(tmp_path):
    store=ApiSettingsStore(tmp_path/'settings.db')
    first=store.save(ApiSettings(api_url='http://localhost:8000/v1/',model='local',api_key='test-secret'))
    assert 'test-secret' not in str(first) and 'api_key' not in first.public()
    assert ApiSettingsStore(store.db_path).get().api_key.get_secret_value()=='test-secret'
    store.save(ApiSettings(api_url='http://localhost/v1',model='other'))
    assert store.get().api_key.get_secret_value()=='test-secret'
    store.save(ApiSettings(api_key=''))
    assert not store.get().public()['api_key_set']


@pytest.mark.anyio
async def test_chat_completions_payload_no_auth_and_usage():
    def respond(request):
        assert request.url.path=='/v1/chat/completions'
        assert 'authorization' not in request.headers
        body=json.loads(request.content)
        assert set(body)=={'model','messages','stream'}
        assert json.loads(body['messages'][1]['content'])=={'data':'Żółć'}
        return httpx.Response(200,json={'choices':[{'message':{'content':'```json\n{"ok":true}\n```'},'finish_reason':'stop'}],
                                       'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}})
    result,elapsed,usage=await chat_request(ApiSettings(api_url='http://local/v1',model='test'),'Instruction',
                                          {'data':'Żółć'},httpx.MockTransport(respond))
    assert result=={'ok':True} and elapsed>=0 and usage['total_tokens']==5


@pytest.mark.anyio
@pytest.mark.parametrize('response',[
    httpx.Response(401,json={'error':'private API error with secret'}),
    httpx.Response(302,headers={'location':'http://elsewhere/v1/chat/completions'}),
    httpx.Response(200,json={'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'length'}]}),
    httpx.Response(200,json={'choices':[{'message':{'content':'not JSON'}}]}),
    httpx.Response(200,json={'choices':[]}),
    httpx.Response(200,json={'choices':[{'message':{'content':'[]'}}]}),
])
async def test_remote_errors_do_not_expose_secrets(response):
    def respond(request):
        assert request.headers['authorization']=='Bearer test-secret'
        return response
    with pytest.raises(AiSyncError) as failure:
        await chat_request(ApiSettings(api_url='http://local/v1/chat/completions',model='test',api_key='test-secret'),
                           'Instruction',{},httpx.MockTransport(respond))
    assert 'secret' not in str(failure.value)


@pytest.mark.anyio
async def test_total_timeout():
    async def respond(request):
        await asyncio.sleep(2)
        return httpx.Response(200,json={})
    with pytest.raises(AiSyncError,match='timeout'):
        await chat_request(ApiSettings(api_url='http://local/v1',model='test',timeout_seconds=1),
                           'Instruction',{},httpx.MockTransport(respond))


@pytest.fixture
def prepared_job(client,settings):
    job_id=str(uuid.uuid4());directory=settings.data_root/'work'/'jobs'/job_id
    en=cue_file(directory/'reference'/'selected'/'selected.eng.ocr.srt','English reference.')
    pl=cue_file(directory/'polish'/'original.pl.srt')
    manifest={'media':{'duration_ms':10000},'reference':{'files':[{'name':en.relative_to(directory).as_posix(),'sha256':sha256(en)}]},
              'polish_candidates':[{'archiveName':'polish/original.pl.srt','sha256':sha256(pl)}]}
    (directory/'manifest.json').write_text(json.dumps(manifest))
    report={'pipeline':'PREPARE_SYNC','selectedEnglish':{'sourceType':'embedded','streamIndex':4},
            'requiresOcr':False,'polishCandidates':manifest['polish_candidates'],'workpack':{}}
    with sqlite3.connect(settings.data_root/'subtitle-agent.db') as db:
        db.execute('INSERT INTO jobs (id,media_path,status,progress,created_at,job_type,task_type,report_json) VALUES (?,?,?,?,?,?,?,?)',
                   (job_id,'unused.mkv','WORKPACK_READY',100,'2026-10-03','PREPARE_WORKPACK','PREPARE_SYNC',json.dumps(report)))
    return job_id,directory


def test_settings_and_connection_api(client,monkeypatch):
    assert client.get('/settings').status_code==200
    assert client.put('/api/settings/ai',json={'api_url':'http://local/v1','model':'test','api_key':'test-secret'}).json()['api_key_set']
    assert 'test-secret' not in client.get('/api/settings/ai').text
    async def fake(settings,instruction,data):return {'ok':True},.125,{'total_tokens':8}
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    assert client.post('/api/settings/ai/test').json()['elapsed_seconds']==.125


def test_sync_uses_prepared_ocr_and_preserves_polish(client,prepared_job,monkeypatch):
    job_id,directory=prepared_job
    original=read_cues(directory/'polish'/'original.pl.srt','pl')[0].raw_text
    async def fake(settings,instruction,data):
        assert data['english'][0]['text']=='English reference.'
        assert data['polish'][0]['id']=='pl:1' and data['polish'][0]['text']==original
        return {'segments':[{'id':'pl:1','start_ms':5000,'end_ms':6500}]},1.25,None
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    response=client.post(f'/api/tasks/{job_id}/ai-sync',json={'polish_file':'polish/original.pl.srt','reference_source_id':'embedded:4'})
    assert response.status_code==200,response.text
    assert response.json()['elapsed_seconds']==1.25
    assert read_cues(directory/'ai-sync.pl.srt','pl')[0].raw_text==original
    assert client.get(f'/api/tasks/{job_id}/ai-sync/download').status_code==200
    assert client.get(f'/api/tasks/{job_id}/ai-sync').json()['result']['cue_count']==1
    # Rebuild/source mutation must invalidate a saved result.
    (directory/'manifest.json').write_text('{}')
    assert client.get(f'/api/tasks/{job_id}/ai-sync/download').status_code==404


def test_invalid_answer_produces_no_srt(client,prepared_job,monkeypatch):
    job_id,directory=prepared_job
    async def fake(*args):return {'segments':[]},.5,None
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    response=client.post(f'/api/tasks/{job_id}/ai-sync',json={'polish_file':'polish/original.pl.srt','reference_source_id':'embedded:4'})
    assert response.status_code==502 and 'Niepełna' in response.json()['detail']['message']
    assert not (directory/'ai-sync.pl.srt').exists()
    assert client.get(f'/api/tasks/{job_id}/ai-sync/download').status_code==404


def test_changed_reference_and_unsafe_polish_are_rejected(client,prepared_job):
    job_id,_=prepared_job
    for file,reference in [('polish/original.pl.srt','embedded:13'),('../private.srt','embedded:4')]:
        response=client.post(f'/api/tasks/{job_id}/ai-sync',json={'polish_file':file,'reference_source_id':reference})
        assert response.status_code==422


@pytest.mark.parametrize("mode",["PREPARE_SYNC","PREPARE_TRANSLATION"])
def test_pipeline_prepares_inputs_for_new_api(client,media_file,monkeypatch,mode):
    import time
    from app.services.subtitle_extraction import SubtitleExtractionResult
    async def probe(path,timeout):
        return {'path':str(path),'name':path.name,'sizeBytes':1,'durationSeconds':100,
                'embeddedSubtitles':[{'streamIndex':4,'subtitleOrder':0,'codec':'subrip','language':'eng',
                                     'title':'English','type':'text','default':True}], 'audioTracks':[]}
    async def extract(reference,media,target,timeout):
        prepared=cue_file(target/'selected.eng.srt','English reference.')
        original=target/'selected.original.srt';original.write_bytes(prepared.read_bytes())
        return SubtitleExtractionResult([original,prepared],[])
    async def fake(settings,instruction,data):
        if mode=='PREPARE_TRANSLATION':
            assert 'polish' not in data
            return {'segments':[{'id':cue['id'],'text':'Przetłumaczona kwestia.'} for cue in data['english']]},.2,None
        return {'segments':[{'id':cue['id'],'start_ms':3000,'end_ms':4500} for cue in data['polish']]},.2,None
    monkeypatch.setattr('app.services.job_manager.probe_media',probe)
    monkeypatch.setattr('app.services.job_manager.extract_embedded',extract)
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    cue_file(media_file.with_suffix('.pl.srt'))
    job_id=client.post('/api/tasks',json={'mediaPath':str(media_file),'mode':mode}).json()['jobId']
    for _ in range(300):
        job=client.get(f'/api/tasks/{job_id}').json()
        if job['status'] in {'WORKPACK_READY','WORKPACK_INCOMPLETE','FAILED'}:break
        time.sleep(.01)
    assert job['status']=='WORKPACK_READY',job
    payload={'reference_source_id':'embedded:4'}
    endpoint='ai-translate' if mode=='PREPARE_TRANSLATION' else 'ai-sync'
    if mode=='PREPARE_SYNC':payload['polish_file']=job['report']['polishCandidates'][0]['archiveName']
    response=client.post(f'/api/tasks/{job_id}/{endpoint}',json=payload)
    assert response.status_code==200,response.text
    assert client.get(f'/api/tasks/{job_id}/{endpoint}/download').status_code==200


@pytest.mark.parametrize('effort',[None,'none'])
@pytest.mark.parametrize('response_format',[None,'json_object'])
def test_reasoning_effort_in_connection_test_and_sync(client,prepared_job,monkeypatch,effort,response_format):
    job_id,_=prepared_job
    calls=[]
    def respond(request):
        body=json.loads(request.content)
        if effort is None:
            assert 'reasoning_effort' not in body
        else:
            assert body['reasoning_effort']=='none'
        if response_format is None:
            assert 'response_format' not in body
        else:
            assert body['response_format']=={'type':'json_object'}
        calls.append(body)
        data=json.loads(body['messages'][1]['content'])
        content={'ok':True} if data.get('test') else {'segments':[{'id':'pl:1','start_ms':3000,'end_ms':4500}]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(content)},'finish_reason':'stop'}]})
    async def request_with_transport(settings,instruction,data):
        return await chat_request(settings,instruction,data,httpx.MockTransport(respond))
    monkeypatch.setattr('app.api.ai_sync.chat_request',request_with_transport)
    settings={'api_url':'http://local/v1','model':'test','reasoning_effort':effort,'response_format':response_format}
    assert client.put('/api/settings/ai',json=settings).json()['reasoning_effort']==effort
    assert client.get('/api/settings/ai').json()['reasoning_effort']==effort
    assert client.get('/api/settings/ai').json()['response_format']==response_format
    assert client.post('/api/settings/ai/test').status_code==200
    response=client.post(f'/api/tasks/{job_id}/ai-sync',json={'polish_file':'polish/original.pl.srt','reference_source_id':'embedded:4'})
    assert response.status_code==200,response.text
    assert len(calls)==2
    # Switching back must stop sending the optional parameter.
    assert client.put('/api/settings/ai',json={**settings,'reasoning_effort':None}).status_code==200
    assert client.get('/api/settings/ai').json()['reasoning_effort'] is None


def test_legacy_settings_default_reasoning_effort(tmp_path):
    store=ApiSettingsStore(tmp_path/'settings.db')
    with sqlite3.connect(store.db_path) as db:
        db.execute('INSERT INTO ai_api_settings VALUES (1,?)',
                   (json.dumps({'api_url':'http://local/v1','model':'test','timeout_seconds':120}),))
    assert ApiSettingsStore(store.db_path).get().reasoning_effort is None
    assert ApiSettingsStore(store.db_path).get().response_format is None
    store.save(ApiSettings(reasoning_effort='none'))
    assert ApiSettingsStore(store.db_path).get().reasoning_effort=='none'


@pytest.mark.anyio
@pytest.mark.parametrize('envelope,expected',[
    ({'choices':[{'message':{'content':None,'reasoning_content':'private reasoning'},'finish_reason':'stop'}]},'pustą treść'),
    ({'choices':[{'message':{'content':'I need to analyze private dialogue.'},'finish_reason':'stop'}]},'tekst zamiast'),
    ({'choices':[{'message':{'content':'{"segments":['},'finish_reason':'stop'}]},'niepoprawny JSON'),
    ({'choices':[]},'choices[0]'),
    ({'choices':[{'message':{'content':[]}}]},'nie jest tekstem'),
])
async def test_format_errors_are_distinct_and_include_elapsed_without_content(envelope,expected):
    with pytest.raises(AiSyncError) as failure:
        await chat_request(ApiSettings(api_url='http://local/v1',model='test'),
                           'Instruction',{},httpx.MockTransport(lambda request:httpx.Response(200,json=envelope)))
    assert expected in str(failure.value)
    assert 'Czas żądania:' in str(failure.value)
    assert 'private' not in str(failure.value)


def test_ai_console_preserves_invalid_model_reply(client,prepared_job,monkeypatch):
    job_id,_=prepared_job
    reply='I cannot synchronize this film. <script>alert(1)</script>'
    envelope={'choices':[{'message':{'content':reply,'reasoning_content':'Model reasoning'},'finish_reason':'stop'}],
              'usage':{'total_tokens':123}}
    async def fake(settings,instruction,data):
        return await chat_request(settings,instruction,data,httpx.MockTransport(lambda request:httpx.Response(200,json=envelope)))
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    client.put('/api/settings/ai',json={'api_url':'http://local/v1','model':'test'})
    response=client.post(f'/api/tasks/{job_id}/ai-sync',json={'polish_file':'polish/original.pl.srt','reference_source_id':'embedded:4'})
    assert response.status_code==502
    entries=client.get('/api/settings/ai/console').json()['entries']
    assert [entry['level'] for entry in entries]==['INFO','INFO','RESPONSE','INFO','ERROR','INFO']
    assert all(entry['job_id']==job_id and entry['operation']=='SYNC' for entry in entries)
    assert reply in entries[2]['message'] and 'Model reasoning' in entries[2]['message']
    assert 'finish_reason' in entries[2]['message'] and '123' in entries[2]['message']
    assert 'tekst zamiast' in entries[-2]['message']
    assert client.delete('/api/settings/ai/console').json()=={'ok':True}
    assert client.get('/api/settings/ai/console').json()['entries']==[]


def test_ai_console_records_test_and_redacts_api_key(client,monkeypatch):
    key='secret-console-token'
    client.put('/api/settings/ai',json={'api_url':'http://local/v1','model':'test','api_key':key})
    async def fake(settings,instruction,data):
        return await chat_request(settings,instruction,data,httpx.MockTransport(
            lambda request:httpx.Response(401,json={'error':f'Invalid bearer {key}'})))
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    assert client.post('/api/settings/ai/test').status_code==502
    response=client.get('/api/settings/ai/console')
    assert key not in response.text and 'UKRYTY KLUCZ API' in response.text
    assert all(row['operation']=='TEST' and row['job_id'] is None for row in response.json()['entries'])
    assert 'HTTP 401' in response.text
    page=client.get('/settings').text
    assert 'Konsola AI' in page and 'id="ai-console"' in page


def test_ai_console_is_bounded_and_persistent(tmp_path):
    from app.services.ai_console import AiConsoleStore, MAX_CHARS, capture, emit
    path=tmp_path/'console.db';store=AiConsoleStore(path)
    key='secret"Ż\n'
    with capture(store,ApiSettings(api_key=key),'TEST'):
        emit('RESPONSE',json.dumps({'error':key},ensure_ascii=True))
        emit('RESPONSE','x'*(MAX_CHARS+500))
    entries=AiConsoleStore(path).read()
    assert 'UKRYTY KLUCZ API' in entries[1]['message']
    assert 'ODPOWIEDŹ SKRÓCONA' in entries[2]['message']
    for index in range(110):store.append('TEST',None,'INFO',str(index))
    entries=AiConsoleStore(path).read()
    assert len(entries)==100 and entries[0]['message']=='10' and entries[-1]['message']=='109'
