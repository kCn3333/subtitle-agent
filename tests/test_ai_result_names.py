from urllib.parse import unquote

from app.services.archive import ArchiveStore
from tests.test_ai_sync import prepared_job
from tests.test_archive import add_job, manager_at


def test_sync_names_versions_dedup_and_archive_download(client,prepared_job,monkeypatch):
    job,directory=prepared_job
    manager=client.app.state.jobs
    media_name='Zażółć (2026) [WEBDL-1080p]'
    with manager._connect() as db:
        db.execute('UPDATE jobs SET media_path=? WHERE id=?',('/media/'+media_name+'.mkv',job))
    start=3000
    async def fake(settings,instruction,data):
        return {'segments':[{'id':'pl:1','start_ms':start,'end_ms':start+1000}]},1,None
    monkeypatch.setattr('app.api.ai_sync.chat_request',fake)
    payload={'polish_file':'polish/original.pl.srt','reference_source_id':'embedded:4'}
    path=f'/api/tasks/{job}/ai-sync'
    first=client.post(path,json=payload)
    assert first.status_code==200,first.text
    expected=f'{media_name}.AI-Synced-v001.pl.srt'
    assert first.json()['filename']==expected
    assert expected in unquote(client.get(path+'/download').headers['content-disposition'])
    assert client.get(path).json()['result']['filename']==expected
    start=5000
    second=client.post(path,json=payload)
    assert second.json()['filename']==f'{media_name}.AI-Synced-v002.pl.srt'
    assert client.post(path,json=payload).json()['filename']==second.json()['filename']
    entries=client.get('/api/archive').json()['titles'][0]['subtitles']
    assert len(entries)==2
    for entry in entries:
        response=client.get(entry['url'])
        assert response.status_code==200
        assert entry['filename'] in unquote(response.headers['content-disposition'])
    # Old summaries without a filename still get a matching download name.
    import json
    summary=directory/'ai-sync.json'
    result=json.loads(summary.read_text());result.pop('filename')
    summary.write_text(json.dumps(result))
    assert client.get(path).json()['result']['filename']==second.json()['filename']
    assert second.json()['filename'] in unquote(client.get(path+'/download').headers['content-disposition'])


def test_archive_names_survive_restart_cross_jobs_and_migrate_legacy_metadata(tmp_path):
    manager=manager_at(tmp_path)
    job,directory=add_job(manager,'Series.S01E02.1080p')
    output=directory/'ai-sync.pl.srt';output.write_text('first')
    first=manager.archive.record(manager.get(job),output,'synchronization')
    assert first=='Series.S01E02.1080p.AI-Synced-v001.pl.srt'
    with manager.archive.connect() as db:
        db.execute("UPDATE archive_files SET filename='ai-sync.pl.srt' WHERE kind='synchronization'")
    manager.archive=ArchiveStore(manager.db_path,manager.settings.data_root)
    entries=manager.archive.list_titles(manager.list_jobs())[0]['subtitles']
    assert entries[0]['filename']==first
    next_job,next_directory=add_job(manager,'Series.S01E02.1080p',1)
    next_output=next_directory/'ai-sync.pl.srt';next_output.write_text('second')
    assert manager.archive.record(manager.get(next_job),next_output,'synchronization')=='Series.S01E02.1080p.AI-Synced-v002.pl.srt'
    next_output.write_text('first')
    assert manager.archive.record(manager.get(next_job),next_output,'synchronization')==first


def test_identical_content_for_different_titles_downloads_with_correct_name(client):
    manager=client.app.state.jobs
    for title in ['FilmA','FilmB']:
        job,directory=add_job(manager,title)
        output=directory/'ai-sync.pl.srt';output.write_text('identical content')
        manager.archive.record(manager.get(job),output,'synchronization')
    titles=client.get('/api/archive').json()['titles']
    for title in titles:
        entry=title['subtitles'][0]
        assert entry['filename']==title['title']+'.AI-Synced-v001.pl.srt'
        response=client.get(entry['url'])
        assert entry['filename'] in unquote(response.headers['content-disposition'])
    bad=entry['url'].split('?')[0]+'?filename=unknown.pl.srt'
    assert client.get(bad).status_code==404
