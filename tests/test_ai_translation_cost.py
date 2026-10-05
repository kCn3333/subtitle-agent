import json
import math

import httpx
import pytest

from app.services.ai_sync import (ApiSettings, AiSyncError, chat_request, usage_metrics,
                                  read_cues, validate_translation)
from tests.test_ai_sync import prepared_job, cue_file


@pytest.mark.parametrize('cost',[0, 0.00001234, 2])
def test_usage_preserves_reported_openrouter_cost(cost):
    usage = usage_metrics({'cost': cost, 'total_tokens': 9}, ApiSettings(api_url='https://openrouter.ai/api/v1'))
    assert usage == {'cost':cost,'cost_currency':'USD','total_tokens':9}


@pytest.mark.parametrize('cost',[None, True, -1, '0.2', math.inf, math.nan, 10**500])
def test_unknown_or_invalid_cost_is_not_free(cost):
    assert usage_metrics({'cost':cost},ApiSettings(api_url='https://other.example/v1')) is None


def test_other_provider_cost_does_not_invent_currency():
    assert usage_metrics({'cost':0},ApiSettings(api_url='http://local/v1')) == {'cost':0}


@pytest.mark.parametrize('rows',[
    [], [{'id':'en:1','text':''}], [{'id':'en:1','text':'PL\n\n2\n00:00:01,000 --> 00:00:02,000\nBad'}],
    [{'id':'en:2','text':'PL'}], [{'id':'en:1','text':'PL'},{'id':'en:1','text':'PL'}],
    [{'id':'en:1','text':'PL','start_ms':1}], [{'id':'en:1','text':'PL\x00'}],
])
def test_translation_rejects_incomplete_or_unsafe_srt(tmp_path,rows):
    english=read_cues(cue_file(tmp_path/'en.srt'),'en')
    with pytest.raises(AiSyncError):validate_translation({'segments':rows},english,10000)


def test_translation_keeps_reference_timing_and_orders_by_original_id(tmp_path):
    path=cue_file(tmp_path/'en.srt')
    english=read_cues(path,'en')
    output=validate_translation({'segments':[{'id':'en:1','text':'Polski tekst.\nDrugi wiersz.'}]},english,10000)
    assert (output[0].start_ms,output[0].end_ms)==(english[0].start_ms,english[0].end_ms)
    assert output[0].raw_text=='Polski tekst.\nDrugi wiersz.'
    with pytest.raises(AiSyncError):validate_translation({'segments':[{'id':'en:1','text':'PL'}]},english,1)


def translate_job(client,prepared_job):
    job_id,directory=prepared_job
    manager=client.app.state.jobs
    job=manager.get(job_id);report=job['report'];report['pipeline']='PREPARE_TRANSLATION'
    with manager._connect() as db:
        db.execute('UPDATE jobs SET task_type=?,report_json=? WHERE id=?',('PREPARE_TRANSLATION',json.dumps(report),job_id))
    data=json.loads((directory/'manifest.json').read_text());data['polish_candidates']=[]
    (directory/'manifest.json').write_text(json.dumps(data))
    return job_id,directory


def use_transport(client,monkeypatch,content):
    client.put('/api/settings/ai',json={'api_url':'https://openrouter.ai/api/v1','model':'provider/test','api_key':'secret'})
    def respond(request):
        payload=json.loads(request.content)
        assert 'usage' not in payload  # No provider-specific request extension.
        assert request.headers['Authorization']=='Bearer secret'
        data=json.loads(payload['messages'][1]['content'])
        assert 'polish' not in data and data['english'][0]['id']=='en:1'
        return httpx.Response(200,json={'choices':[{'message':{'content':content},'finish_reason':'stop'}],
                              'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15,'cost':0.00123456}})
    async def call(settings,instruction,data):
        return await chat_request(settings,instruction,data,httpx.MockTransport(respond))
    monkeypatch.setattr('app.api.ai_sync.chat_request',call)


def test_translation_cost_console_download_archive_and_restoration(client,prepared_job,monkeypatch):
    job,directory=translate_job(client,prepared_job)
    use_transport(client,monkeypatch,json.dumps({'segments':[{'id':'en:1','text':'Polskie tłumaczenie.'}]}))
    response=client.post(f'/api/tasks/{job}/ai-translate',json={'reference_source_id':'embedded:4'})
    assert response.status_code==200,response.text
    assert response.json()['total_cost']==0.00123456
    assert response.json()['usage']['cost_currency']=='USD'
    assert response.json()['mode']=='translation'
    assert read_cues(directory/'ai-translation.pl.srt','pl')[0].raw_text=='Polskie tłumaczenie.'
    assert client.get(f'/api/tasks/{job}/ai-translate/download').status_code==200
    assert client.get(f'/api/tasks/{job}/ai-translate').json()['result']['usage']['cost']==0.00123456
    assert client.get(f'/api/tasks/{job}/ai-sync').status_code==422
    entries=client.get('/api/settings/ai/console').json()['entries']
    metrics=next(e['message'] for e in entries if 'tokeny:' in e['message'])
    assert 'Czas żądania:' in metrics and 'koszt: 0.00123456 USD' in metrics
    assert 'Koszt całej operacji: 0.00123456 USD' in entries[-1]['message']
    assert all(e['operation']=='TRANSLATE' for e in entries)
    subtitles=client.get('/api/archive').json()['titles'][0]['subtitles']
    assert subtitles[0]['kind']=='translation'
    assert client.get(subtitles[0]['url']).status_code==200


@pytest.mark.parametrize('content',['plain text','{"segments":[]}'])
def test_paid_bad_translation_keeps_cost_and_creates_no_srt(client,prepared_job,monkeypatch,content):
    job,directory=translate_job(client,prepared_job)
    use_transport(client,monkeypatch,content)
    response=client.post(f'/api/tasks/{job}/ai-translate',json={'reference_source_id':'embedded:4'})
    assert response.status_code==502,response.text
    assert response.json()['detail']['usage']['cost']==0.00123456
    assert not (directory/'ai-translation.pl.srt').exists()
    entries=client.get('/api/settings/ai/console').json()['entries']
    assert any(e['level']=='ERROR' for e in entries)
    assert 'Koszt całej operacji: 0.00123456 USD' in entries[-1]['message']


def test_translation_preserves_order_when_model_reorders_ids(tmp_path):
    path=cue_file(tmp_path/'en.srt')
    with path.open('a') as handle:handle.write('\n8\n00:00:03,000 --> 00:00:04,000\nSecond English.\n')
    english=read_cues(path,'en')
    result={'segments':[{'id':'en:2','text':'Drugie.'},{'id':'en:1','text':'Pierwsze.'}]}
    output=validate_translation(result,english,10000)
    assert [cue.raw_text for cue in output]==['Pierwsze.','Drugie.']
    assert [(cue.start_ms,cue.end_ms) for cue in output]==[(1000,2000),(3000,4000)]


@pytest.mark.anyio
async def test_cost_summaries_are_isolated_between_concurrent_jobs(tmp_path):
    import asyncio
    from app.services.ai_console import AiConsoleStore, capture, record_metrics
    store=AiConsoleStore(tmp_path/'costs.db')
    async def operation(job,cost):
        with capture(store,ApiSettings(model='test'),'SYNC',job):
            await asyncio.sleep(0)
            record_metrics(1,{'cost':cost,'cost_currency':'USD'})
            await asyncio.sleep(0)
    await asyncio.gather(operation('job-A',0),operation('job-B',0.002))
    summary={row['job_id']:row['message'] for row in store.read() if row['level'] == 'SUMMARY'}
    assert summary['job-A'].endswith('0 USD') and summary['job-B'].endswith('0.002 USD')


def test_sync_and_connection_test_preserve_zero_cost(client,prepared_job,monkeypatch):
    job,_=prepared_job
    client.put('/api/settings/ai',json={'api_url':'https://openrouter.ai/api/v1','model':'provider/test'})
    def respond(request):
        data=json.loads(json.loads(request.content)['messages'][1]['content'])
        content={'ok':True} if data.get('test') else {'segments':[{'id':'pl:1','start_ms':3000,'end_ms':4500}]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(content)},'finish_reason':'stop'}],
                              'usage':{'total_tokens':10,'cost':0}})
    async def call(settings,instruction,data):
        return await chat_request(settings,instruction,data,httpx.MockTransport(respond))
    monkeypatch.setattr('app.api.ai_sync.chat_request',call)
    connection=client.post('/api/settings/ai/test')
    assert connection.json()['usage']['cost']==0
    response=client.post(f'/api/tasks/{job}/ai-sync',json={'reference_source_id':'embedded:4','polish_file':'polish/original.pl.srt'})
    assert response.status_code==200 and response.json()['total_cost']==0
    entries=client.get('/api/settings/ai/console').json()['entries']
    summaries=[row for row in entries if row['level'] == 'SUMMARY']
    assert len(summaries)==2 and all(row['message'].endswith('0 USD') for row in summaries)
