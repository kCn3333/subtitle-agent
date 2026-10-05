import json

import pytest

from app.services.media_analysis import (_flags, rank_references, discover_external_subtitles_with_rejections,
                                        parse_media_identity)
from app.services.subtitle_extraction import SubtitleExtractionResult, extract_subtitle
from app.services.workpack import sha256_file
from tests.test_ai_sync import prepared_job
from tests.test_workpack_pipelines import _wait, _embedded


def test_reference_ranking_keeps_english_priority_and_exposes_other_languages():
    french = {**_embedded(), 'streamIndex': 3, 'language': 'fre', 'title': 'French Full Dialogue'}
    german = {**french, 'streamIndex': 4, 'language': 'ger', 'title': 'German'}
    rows = rank_references([french, german, _embedded()], [])
    assert [row['language'] for row in rows] == ['eng', 'fra', 'deu']
    assert {row['streamIndex'] for row in rows} == {2, 3, 4}
    assert rank_references([french], [])[0]['language'] == 'fra'


def test_external_french_matches_media_without_mistaking_it_for_a_language(tmp_path):
    media = tmp_path / 'It.mkv'; media.write_bytes(b'media')
    (tmp_path / 'It.fr.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n')
    found, rejected, ignored = discover_external_subtitles_with_rejections(media)
    assert len(found) == 1 and not rejected and not ignored
    assert found[0]['languageHint'] == 'fr'
    assert _flags('It.Follows.srt')['languageHint'] is None
    assert _flags('It.srt')['languageHint'] is None
    assert _flags('English.Patient.fr.srt')['languageHint'] == 'fr'
    assert parse_media_identity('It.fr.srt').normalized_title == parse_media_identity('It.mkv').normalized_title == 'it'
    assert rank_references([], found)[0]['language'] == 'fra'


@pytest.mark.parametrize('mode', ['PREPARE_SYNC', 'PREPARE_TRANSLATION'])
def test_french_reference_preparation_and_manual_selection(client, settings, monkeypatch, mode):
    media = settings.media_roots[0] / 'Film.mkv'; media.write_bytes(b'media')
    (media.parent / 'Film.pl.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nDzień dobry.\n')
    french = {**_embedded(), 'language': 'fra', 'title': 'French', 'streamIndex': 3}
    async def probe(path, timeout):
        return {'path': str(path), 'name': path.name, 'sizeBytes': 5, 'durationSeconds': 100,
                'embeddedSubtitles': [french], 'audioTracks': []}
    async def extract(reference, media_path, target, timeout):
        assert reference['language'] == 'fra'
        target.mkdir(parents=True, exist_ok=True)
        path = target / 'selected.ref.srt'
        path.write_text('1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n')
        return SubtitleExtractionResult([path], [])
    monkeypatch.setattr('app.services.job_manager.probe_media', probe)
    monkeypatch.setattr('app.services.job_manager.extract_embedded', extract)
    job_id = client.post('/api/workpacks', json={'mediaPath': str(media), 'taskType': mode}).json()['jobId']
    job = _wait(client, job_id)
    assert job['status'] == 'WORKPACK_READY', job
    assert job['report']['selectedReference']['language'] == 'fra'
    assert job['report']['referenceRanking'][0]['streamIndex'] == 3
    directory = settings.data_root / 'work' / 'jobs' / job_id
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['reference']['language'] == 'fra'
    assert 'reference/selected/selected.ref.srt' in manifest['files']
    assert 'Angielskie' not in (directory / 'REQUEST.md').read_text()
    response = client.post(f'/api/workpacks/{job_id}/reference', json={'referenceSourceId': 'embedded:3'})
    assert response.status_code == 202, response.text


@pytest.mark.anyio
async def test_external_french_extraction_uses_neutral_name(tmp_path):
    media = tmp_path / 'Film.mkv'; media.write_bytes(b'media')
    source = tmp_path / 'Film.fr.srt'; source.write_text('1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n')
    result = await extract_subtitle({'sourceType': 'external', 'path': str(source), 'format': 'srt',
                                    'language': 'fra'}, media, tmp_path / 'reference', 1)
    assert [path.name for path in result.files] == ['selected.original.srt', 'selected.ref.srt']
    assert result.files[-1].read_bytes() == source.read_bytes()


@pytest.mark.parametrize('translation', [False, True])
def test_ai_accepts_french_reference_and_preserves_output_contract(client, prepared_job, monkeypatch, translation):
    job_id, directory = prepared_job
    old = directory / 'reference' / 'selected' / 'selected.eng.ocr.srt'
    source = old.with_name('selected.ref.srt'); old.rename(source)
    source.write_text('1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n')
    manifest = json.loads((directory / 'manifest.json').read_text())
    manifest['reference'] = {'language': 'fra', 'files': [{'name': source.relative_to(directory).as_posix(), 'sha256': sha256_file(source)}]}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    manager = client.app.state.jobs
    report = manager.get(job_id)['report']
    report['selectedReference'] = {'sourceType': 'embedded', 'streamIndex': 4, 'language': 'fra'}
    if translation: report['pipeline'] = 'PREPARE_TRANSLATION'
    with manager._connect() as db:
        db.execute('UPDATE jobs SET report_json=?,task_type=? WHERE id=?',
                   (json.dumps(report), report['pipeline'], job_id))
    async def call(settings, instruction, data):
        assert data['reference_language'] == 'fra'
        assert data['reference'][0]['id'] == 'ref:1'
        assert data['reference'][0]['text'] == 'Bonjour.'
        assert 'English' not in instruction
        return ({'segments': [{'id': 'ref:1', 'text': 'Dzień dobry.'}]} if translation else
                {'segments': [{'id': 'pl:1', 'start_ms': 1000, 'end_ms': 2000}]}), .1, None
    monkeypatch.setattr('app.api.ai_sync.chat_request', call)
    endpoint = 'ai-translate' if translation else 'ai-sync'
    response = client.post(f'/api/tasks/{job_id}/{endpoint}', json={'reference_source_id': 'embedded:4',
                           **({} if translation else {'polish_file': 'polish/original.pl.srt'})})
    assert response.status_code == 200, response.text
    assert client.get(f'/api/tasks/{job_id}/{endpoint}/download').status_code == 200
    content = (directory / ('ai-translation.pl.srt' if translation else 'ai-sync.pl.srt')).read_text()
    assert '00:00:01,000 --> 00:00:02,000' in content
    assert ('Dzień dobry.' in content) if translation else ('Bonjour.' not in content)


def test_manual_rebuild_can_switch_from_english_to_french(client, settings, monkeypatch):
    media = settings.media_roots[0] / 'Film.mkv'; media.write_bytes(b'media')
    french = {**_embedded(), 'streamIndex': 3, 'language': 'fra', 'title': 'French'}
    async def probe(path, timeout):
        return {'path': str(path), 'name': path.name, 'durationSeconds': 100,
                'embeddedSubtitles': [_embedded(), french], 'audioTracks': []}
    async def extract(reference, media_path, target, timeout):
        from app.services.subtitle_languages import reference_prefix
        target.mkdir(parents=True, exist_ok=True)
        path = target / f'{reference_prefix(reference)}.srt'
        path.write_text('1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n')
        return SubtitleExtractionResult([path], [])
    monkeypatch.setattr('app.services.job_manager.probe_media', probe)
    monkeypatch.setattr('app.services.job_manager.extract_embedded', extract)
    job_id = client.post('/api/workpacks', json={'mediaPath': str(media), 'taskType': 'PREPARE_TRANSLATION'}).json()['jobId']
    job = _wait(client, job_id)
    assert job['report']['selectedReference']['language'] == 'eng'
    # Older saved reports should expose newly supported tracks as well.
    saved = {key: value for key, value in job['report'].items() if key not in {'referenceRanking', 'selectedReference'}}
    with client.app.state.jobs._connect() as db:
        db.execute('UPDATE jobs SET report_json=? WHERE id=?', (json.dumps(saved), job_id))
    assert len(client.get(f'/api/workpacks/{job_id}').json()['report']['referenceRanking']) == 2
    response = client.post(f'/api/workpacks/{job_id}/reference', json={'referenceSourceId': 'embedded:3'})
    assert response.status_code == 202, response.text
    import time
    for _ in range(300):
        job = client.get(f'/api/workpacks/{job_id}').json()
        if job['status'] == 'WORKPACK_READY' and job['report']['selectedReference']['language'] == 'fra':
            break
        time.sleep(.01)
    assert job['report']['selectedReference']['language'] == 'fra', job
    assert 'reference/selected/selected.ref.srt' in job['report']['workpack']['files']
    assert 'reference/selected/selected.eng.srt' not in job['report']['workpack']['files']


@pytest.mark.anyio
async def test_ocr_client_passes_reference_language(tmp_path, monkeypatch):
    import base64
    import httpx
    from app.services.ocr_client import recognize_reference
    source = tmp_path / 'selected.ref.sup'; source.write_bytes(b'sup')
    original = httpx.AsyncClient
    def respond(request):
        assert request.headers['X-OCR-Language'] == 'fra'
        return httpx.Response(200, json={'srtBase64': base64.b64encode(b'1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n').decode(), 'cueCount': 1})
    monkeypatch.setattr('app.services.ocr_client.httpx.AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    result = await recognize_reference([source], 'http://ocr', 1, 1024, language='fra')
    assert b'Bonjour.' in result.content


def test_french_ocr_quality_does_not_apply_english_dictionary():
    from app.services.ocr_quality import quality_report
    report = quality_report('1\n00:00:01,000 --> 00:00:02,000\nBonjour monsieur.\n'.encode(), None,
                            dictionary={'hello', 'sir'}, language='fra')
    assert report['textQuality'] == 'UNKNOWN', report
    assert report['dictionaryAvailable'] is False
    assert report['outOfDictionaryWordRatio'] is None


@pytest.mark.parametrize('mode', ['PREPARE_SYNC', 'PREPARE_TRANSLATION'])
def test_french_graphic_reference_uses_french_ocr_without_english_text_fixes(client, settings, monkeypatch, mode):
    from app.services.ocr_client import OcrResult
    media = settings.media_roots[0] / 'Film.mkv'; media.write_bytes(b'media')
    (media.parent / 'Film.pl.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nDzień dobry.\n')
    settings.ocr_worker_url = 'http://ocr'
    french = {**_embedded('graphic'), 'language': 'fra', 'title': 'French'}
    async def probe(path, timeout):
        return {'path': str(path), 'name': path.name, 'durationSeconds': 100,
                'embeddedSubtitles': [french], 'audioTracks': []}
    async def extract(reference, media_path, target, timeout):
        target.mkdir(parents=True, exist_ok=True)
        path = target / 'selected.ref.sup'; path.write_bytes(b'sup')
        return SubtitleExtractionResult([path], [])
    async def graphic(*args):
        return {'events': [{'start_ms': 1000}], 'event_count': 1}
    content = '1\n00:00:01,000 --> 00:00:02,000\nBonjour.\n'.encode()
    async def recognize(paths, url, timeout, maximum_output_bytes, language):
        assert paths[0].name == 'selected.ref.sup' and language == 'fra'
        return OcrResult(content, 'seconv+tesseract', 1, 0, 1000, 1000, 2000, [])
    def fail_normalization(*args):
        raise AssertionError('French OCR must not use English normalization')
    monkeypatch.setattr('app.services.job_manager.probe_media', probe)
    monkeypatch.setattr('app.services.job_manager.extract_embedded', extract)
    monkeypatch.setattr('app.services.job_manager.graphic_timeline', graphic)
    monkeypatch.setattr('app.services.job_manager.recognize_reference', recognize)
    monkeypatch.setattr('app.services.job_manager.normalize_ocr_text', fail_normalization)
    job_id = client.post('/api/workpacks', json={'mediaPath': str(media), 'taskType': mode}).json()['jobId']
    job = _wait(client, job_id)
    assert job['status'] == 'WORKPACK_READY', job
    assert job['report']['requiresOcr'] is False
    directory = settings.data_root / 'work' / 'jobs' / job_id
    assert (directory / 'reference' / 'selected' / 'selected.ref.ocr.srt').read_bytes() == content
    assert 'reference/selected/selected.ref.ocr.srt' in job['report']['workpack']['files']
    request = (directory / 'REQUEST.md').read_text()
    assert 'selected.ref.ocr.srt' in request and 'selected.eng' not in request
