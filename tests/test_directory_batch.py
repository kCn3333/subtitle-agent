import asyncio
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.directory_batch import scan_directory
from app.services.media_analysis import UserInputError, discover_external_subtitles
from app.services.subtitle_extraction import SubtitleExtractionResult
from app.services.system_probe import ToolInfo
from app.services.workpack import validate_zip
from tests.conftest import authenticate


def season(settings, count=3):
    directory = settings.media_roots[0] / 'Example Show (2020)' / 'Season 01'
    directory.mkdir(parents=True)
    for index in range(1, count + 1):
        stem = f'Example.Show.S01E{index:02d}.WEB'
        (directory / (stem + '.mkv')).write_bytes(b'video')
        (directory / (stem + '.pl.srt')).write_text(
            f'1\n00:00:01,000 --> 00:00:02,000\nPolski tekst odcinka {index}.\n')
    return directory


def configure(monkeypatch, ambiguous=False, external=False, fail_episode=None):
    probes, extracted = [], []
    async def probe(path, timeout):
        probes.append(path.name)
        if fail_episode and fail_episode in path.name:
            raise ValueError('synthetic probe failure')
        tracks = [] if external else [{'streamIndex': 1, 'codec': 'subrip', 'type': 'text',
                                       'language': 'eng', 'title': 'Full Dialogue'}]
        if ambiguous and 'E01.' in path.name:
            tracks.append({**tracks[0], 'streamIndex': 2})
        return {'name': path.name, 'path': str(path), 'durationSeconds': 100,
                'sizeBytes': path.stat().st_size, 'container': 'matroska', 'width': 1920, 'height': 1080,
                'videoCodec': 'h264', 'avgFrameRate': '25/1' if 'E02.' in path.name else '24000/1001',
                'audioTracks': [], 'embeddedSubtitles': tracks}
    async def extract(reference, media_path, target, timeout):
        extracted.append((media_path.name, reference.get('streamIndex')))
        target.mkdir(parents=True, exist_ok=True)
        path = target / 'selected.eng.srt'
        path.write_text(f'1\n00:00:01,000 --> 00:00:02,000\nReference {media_path.name} stream {reference.get("streamIndex")}\n')
        return SubtitleExtractionResult([path], [])
    monkeypatch.setattr('app.services.job_manager.probe_media', probe)
    monkeypatch.setattr('app.services.job_manager.extract_embedded', extract)
    return probes, extracted


def wait_batch(client, job_id, statuses=('WORKPACK_READY', 'WORKPACK_INCOMPLETE', 'BATCH_PAUSED', 'FAILED', 'INSPECTION_READY')):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f'/api/tasks/{job_id}')
        assert response.status_code == 200
        body = response.json()
        if body['status'] in statuses:
            return body
        time.sleep(.02)
    pytest.fail(f'Directory batch timeout: {body}')


def submit(client, directory, mode='PREPARE_SYNC'):
    response = client.post('/api/tasks', json={'mediaPath': str(directory), 'mode': mode})
    assert response.status_code == 202, response.text
    assert response.json()['jobType'] == 'DIRECTORY_WORKPACK'
    return response.json()['jobId']


def test_scan_sorts_episodes_skips_extras_and_symlinks_and_enforces_limit(tmp_path):
    root = tmp_path / 'Show'; root.mkdir()
    for name in ('Show.S01E10.mkv', 'Show.S01E02.mkv', 'Show.S01E01-E02.mkv', 'sample.mp4', 'Show.trailer.mkv'):
        (root / name).write_bytes(b'x')
    extras = root / 'extras'; extras.mkdir(); (extras / 'extra.mkv').write_bytes(b'x')
    outside = tmp_path / 'private'; outside.mkdir(); (outside / 'secret.mkv').write_bytes(b'x')
    (root / 'linked').symlink_to(outside)
    (root / 'linked.mkv').symlink_to(outside / 'secret.mkv')
    items, ignored = scan_directory(root, 3)
    assert [item['identity']['episode'] for item in items] == [1, 2, 10]
    assert items[0]['identity']['episode_end'] == 2
    assert ignored == 3
    with pytest.raises(UserInputError, match='limit'):
        scan_directory(root, 2)


def test_sidecar_matching_prefers_exact_version_and_never_confuses_episode_numbers(tmp_path):
    videos = [tmp_path / name for name in ('Show.S01E01.WEB.mkv', 'Show.S01E01.Bluray.mkv', 'Show.S01E010.mkv')]
    for video in videos:
        video.write_bytes(b'v')
    for name in ('Show.S01E01.WEB.pl.srt', 'Show.S01E01.pl.srt', 'Show.S01E010.pl.srt',
                 'Show.S01E01.WEB.en.foreign.Custom Title.srt'):
        (tmp_path / name).write_text('1\n00:00:01,000 --> 00:00:02,000\nTest\n')
    found = discover_external_subtitles(videos[0])
    assert 'Show.S01E010.pl.srt' not in {item['name'] for item in found}
    assert next(item for item in found if item['name'] == 'Show.S01E01.WEB.pl.srt')['identityMatch']['automatic'] is True
    assert next(item for item in found if item['name'] == 'Show.S01E01.pl.srt')['identityMatch']['automatic'] is False
    assert next(item for item in found if 'foreign' in item['name'])['forced'] is True
    bluray = discover_external_subtitles(videos[1])
    assert 'Show.S01E01.WEB.pl.srt' not in {item['name'] for item in bluray}


def test_directory_larger_than_queue_produces_one_complete_zip(client, settings, monkeypatch, tmp_path):
    directory = season(settings, 7)
    probes, extracted = configure(monkeypatch)
    before = {path: path.read_bytes() for path in directory.iterdir()}
    job_id = submit(client, directory)
    body = wait_batch(client, job_id)
    assert body['status'] == 'WORKPACK_READY', body
    items = body['report']['batch']['items']
    assert len(items) == 7 and all(item['state'] == 'ready' for item in items)
    assert len(probes) == len(extracted) == 7
    assert body['report']['summary']['common']['width'] == 1920
    assert len(body['report']['summary']['differences']['fps']) == 2
    result = client.get(f'/api/tasks/{job_id}/download')
    assert result.status_code == 200
    assert hashlib.sha256(result.content).hexdigest() == result.headers['x-workpack-sha256']
    archive_path = tmp_path / 'result.zip'; archive_path.write_bytes(result.content)
    validate_zip(archive_path)
    with zipfile.ZipFile(io.BytesIO(result.content)) as bundle:
        names = bundle.namelist()
        assert not any(name.endswith(('.zip', '.mkv')) for name in names)
        manifest = json.loads(bundle.read('manifest.json'))
        assert len(manifest['items']) == 7
        assert set(manifest['files']) == set(names)
        assert str(settings.media_roots[0]) not in json.dumps(manifest)
        for index, item in enumerate(manifest['items'], 1):
            child = item['manifest']
            for artifact in child['reference']['files'] + child['polish_candidates']:
                name = artifact.get('archiveName') or artifact['name']
                assert hashlib.sha256(bundle.read(name)).hexdigest() == artifact['sha256']
            pl = bundle.read(child['polish_candidates'][0]['archiveName']).decode()
            assert f'odcinka {index}.' in pl
    titles = client.get('/api/archive').json()['titles']
    assert len(titles) == 1
    assert before == {path: path.read_bytes() for path in directory.iterdir()}


def test_ambiguity_pauses_before_extraction_and_confirmation_resumes_whole_batch(client, settings, monkeypatch):
    directory = season(settings, 2)
    probes, extracted = configure(monkeypatch, ambiguous=True)
    job_id = submit(client, directory)
    body = wait_batch(client, job_id)
    assert body['status'] == 'BATCH_PAUSED'
    assert len(probes) == 1 and extracted == []
    assert body['report']['batch']['items'][1]['jobId'] is None
    assert submit(client, directory) == job_id
    invalid = client.post(f'/api/tasks/{job_id}/batch', json={'action': 'reference', 'referenceSourceId': 'embedded:999'})
    assert invalid.status_code == 422
    response = client.post(f'/api/tasks/{job_id}/batch', json={'action': 'reference', 'referenceSourceId': 'embedded:2'})
    assert response.status_code == 202
    assert wait_batch(client, job_id)['status'] == 'WORKPACK_READY'
    assert extracted[0][1] == 2
    assert len(probes) == 2  # Saved analysis is reused after explicit selection.


def test_user_skips_finish_as_ready_with_explicit_exclusions(client, settings, monkeypatch):
    directory = season(settings, 2)
    configure(monkeypatch, external=True)
    job_id = submit(client, directory)
    assert wait_batch(client, job_id)['status'] == 'BATCH_PAUSED'
    assert client.post(f'/api/tasks/{job_id}/batch', json={'action': 'skip'}).status_code == 202
    assert wait_batch(client, job_id)['status'] == 'BATCH_PAUSED'
    client.post(f'/api/tasks/{job_id}/batch', json={'action': 'skip'})
    assert wait_batch(client, job_id)['status'] == 'WORKPACK_READY'
    with zipfile.ZipFile(io.BytesIO(client.get(f'/api/tasks/{job_id}/download').content)) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        assert all(item['state'] == 'skipped' for item in manifest['items'])
        assert manifest['warnings'] == []
        assert manifest['complete'] is True
        assert manifest['skippedCount'] == 2


def test_failed_episode_does_not_stop_siblings_and_archive_limits_never_silently_omit(client, settings, monkeypatch):
    directory = season(settings)
    configure(monkeypatch, fail_episode='E02.')
    settings.directory_max_files = 3
    job_id = submit(client, directory)
    body = wait_batch(client, job_id)
    assert body['status'] == 'FAILED'
    assert [item['state'] for item in body['report']['batch']['items']] == ['ready', 'failed', 'ready']
    assert not body['report'].get('workpack')
    settings.directory_max_files = 10000
    assert client.post(f'/api/tasks/{job_id}/batch', json={'action': 'resume'}).status_code == 202
    body = wait_batch(client, job_id)
    assert body['status'] == 'WORKPACK_INCOMPLETE'
    with zipfile.ZipFile(io.BytesIO(client.get(f'/api/tasks/{job_id}/download').content)) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        assert manifest['items'][1]['state'] == 'failed'
        assert len([item for item in manifest['items'] if 'manifest' in item]) == 2


def test_inspection_directory_has_compact_report_without_artifacts(client, settings, monkeypatch):
    directory = season(settings, 2)
    configure(monkeypatch, external=True)
    body = wait_batch(client, submit(client, directory, mode='INSPECT'))
    assert body['status'] == 'INSPECTION_READY'
    assert body['report']['summary']['common']['width'] == 1920
    assert not body['report'].get('workpack')


def test_directory_rejects_paths_outside_media_roots(client, tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir()
    assert client.post('/api/tasks', json={'mediaPath': str(outside), 'mode': 'PREPARE_SYNC'}).status_code == 422


def test_restart_preserves_completed_items_and_resumes_interrupted_item(settings, monkeypatch):
    directory = season(settings, 3)
    monkeypatch.setattr('app.main.probe_tools', lambda: ToolInfo('ffmpeg', 'ffprobe', 'mkvextract'))
    probes, extracted = configure(monkeypatch)
    from app.services import job_manager
    normal_probe = job_manager.probe_media
    block = True
    async def blocking_probe(path, timeout):
        if block and 'E02.' in path.name:
            await asyncio.Event().wait()
        return await normal_probe(path, timeout)
    monkeypatch.setattr(job_manager, 'probe_media', blocking_probe)
    with TestClient(create_app(settings)) as first:
        authenticate(first)
        job_id = submit(first, directory)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            body = first.get(f'/api/tasks/{job_id}').json()
            if body['report']['batch']['current'] == 1:
                break
            time.sleep(.02)
        else:
            pytest.fail('Second episode never started')
    block = False
    with TestClient(create_app(settings)) as second:
        authenticate(second)
        body = second.get(f'/api/tasks/{job_id}').json()
        assert body['status'] == 'INTERRUPTED'
        assert body['report']['batch']['items'][0]['state'] == 'ready'
        assert second.post(f'/api/tasks/{job_id}/batch', json={'action': 'resume'}).status_code == 202
        assert wait_batch(second, job_id)['status'] == 'WORKPACK_READY'
    assert len([name for name in probes if 'E01.' in name]) == 1
    assert len(extracted) == 3


def test_real_ffprobe_extraction_and_combined_zip_before_merge(settings, monkeypatch, require_tools, tmp_path):
    import subprocess
    directory = season(settings, 2)
    reference = tmp_path / 'english.srt'
    reference.write_text('1\n00:00:00,100 --> 00:00:01,800\nA real embedded reference line.\n')
    for video in directory.glob('*.mkv'):
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=16x16:rate=1:duration=2',
                        '-i', str(reference), '-map', '0:v', '-map', '1:s', '-c:v', 'mpeg4', '-c:s', 'srt',
                        '-metadata:s:s:0', 'language=eng', '-y', str(video)],
                       check=True, capture_output=True, timeout=15)
    monkeypatch.setattr('app.main.probe_tools', lambda: ToolInfo('ffmpeg', 'ffprobe', 'mkvextract'))
    with TestClient(create_app(settings)) as client:
        authenticate(client)
        body = wait_batch(client, submit(client, directory))
        assert body['status'] == 'WORKPACK_READY', body
        result = client.get(f'/api/tasks/{body["jobId"]}/download')
        archive = tmp_path / 'real-directory.zip'; archive.write_bytes(result.content)
        validate_zip(archive)
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read('manifest.json'))
            assert len(manifest['items']) == 2
            for item in manifest['items']:
                ref = item['manifest']['reference']['files'][0]
                assert 'A real embedded reference line.' in bundle.read(ref['name']).decode()


def test_paused_batch_and_its_children_survive_retention_beyond_thirty_titles(settings):
    from app.services.job_manager import JobManager
    settings.data_root.mkdir()
    manager = JobManager(settings.data_root / 'test.db', settings)
    with manager._connect() as db:
        db.execute("INSERT INTO jobs (id,media_path,status,progress,created_at,job_type) VALUES "
                   "('parent','/media/old-series','BATCH_PAUSED',35,'2020-01-01','DIRECTORY_WORKPACK')")
        db.execute("INSERT INTO jobs (id,media_path,status,progress,created_at,batch_parent_id) VALUES "
                   "('child','/media/old-series/E01.mkv','WORKPACK_READY',100,'2020-01-01','parent')")
        for index in range(35):
            db.execute("INSERT INTO jobs (id,media_path,status,progress,created_at) VALUES (?,?,?,100,'2026-01-01')",
                       (str(index), f'/media/movie-{index}.mkv', 'COMPLETED'))
    manager.cleanup_expired_artifacts()
    assert manager.get('parent') is not None
    assert manager.get('child') is not None


def test_scan_sorts_seasons_numerically_and_sidecars_infer_bare_episode_names(tmp_path):
    root = tmp_path / 'Show'; root.mkdir()
    for number in (10, 2):
        directory = root / f'Season {number}'; directory.mkdir()
        video = directory / f'S{number:02d}E01.mkv'; video.write_bytes(b'x')
        subtitle = directory / f'Show.S{number:02d}E01.Release.fra.hi.Commentary.srt'
        subtitle.write_text('1\n00:00:01,000 --> 00:00:02,000\nBonjour\n')
        matches = discover_external_subtitles(video)
        assert len(matches) == 1
        assert matches[0]['identityMatch']['automatic'] is True
        assert matches[0]['languageHint'] == 'fr'
        assert matches[0]['hearingImpaired'] is True
    items, _ = scan_directory(root, 10)
    assert [item['identity']['season'] for item in items] == [2, 10]


def test_external_reference_requires_explicit_confirmation_in_batch(client, settings, monkeypatch):
    directory = season(settings, 1)
    configure(monkeypatch, external=True)
    ref = directory / 'Example.Show.S01E01.WEB.en.srt'
    ref.write_text('1\n00:00:01,000 --> 00:00:02,000\nExternal reference\n')
    job_id = submit(client, directory)
    paused = wait_batch(client, job_id)
    assert paused['status'] == 'BATCH_PAUSED'
    assert paused['report']['attention']['externalReferenceConfirmationRequired'] is True
    response = client.post(f'/api/tasks/{job_id}/batch', json={
        'action': 'reference', 'referenceSourceId': 'external:' + ref.name})
    assert response.status_code == 202
    assert wait_batch(client, job_id)['status'] == 'WORKPACK_READY'


def test_retry_only_failed_items_keeps_successful_results(client, settings, monkeypatch):
    directory = season(settings, 3)
    configure(monkeypatch, fail_episode='E02.')
    job_id = submit(client, directory)
    assert wait_batch(client, job_id)['status'] == 'WORKPACK_INCOMPLETE'
    probes, extracted = configure(monkeypatch)
    assert client.post(f'/api/tasks/{job_id}/batch', json={'action': 'retry'}).status_code == 202
    assert wait_batch(client, job_id)['status'] == 'WORKPACK_READY'
    assert len(probes) == len(extracted) == 1
    assert 'E02.' in probes[0]


def test_subtitle_summary_distinguishes_missing_sources_flags_and_technical_findings():
    from app.services.directory_batch import compact_summary
    reference = {'language': 'eng', 'codec': 'subrip', 'type': 'text', 'forced': False}
    summary = compact_summary([
        ('E01.mkv', {'pipeline': 'INSPECT', 'selectedReference': reference,
                     'polishCandidateInspection': [{'name': 'E01.pl.srt', 'eligibleByDefault': True}]}),
        ('E02.mkv', {'pipeline': 'INSPECT', 'selectedReference': {**reference, 'type': 'graphic', 'forced': True},
                     'polishCandidateInspection': [{'name': 'E02.pl.srt', 'eligibleByDefault': False}]}),
        ('E03.mkv', {'pipeline': 'INSPECT', 'selectedReference': None, 'embeddedSubtitleTracks': [{'language': 'pol'}], 'incompatiblePolishCandidates': [{'name': 'wrong.pl.srt'}]}),
    ])['subtitleInspection']
    assert (summary['total'], summary['withReference'], summary['withPolish'], summary['needsAttention']) == (3, 2, 2, 2)
    assert summary['withExternalPolish'] == 1
    assert summary['items'][2]['embeddedPolishCount'] == 1
    assert summary['items'][0]['attentionReasons'] == []
    assert summary['items'][0]['polishFiles'] == ['E01.pl.srt']
    assert any('forced' in reason for reason in summary['items'][1]['attentionReasons'])
    assert any('OCR' in reason for reason in summary['items'][1]['attentionReasons'])
    assert summary['items'][2]['incompatiblePolishCount'] == 1


def test_cached_inspection_report_gains_subtitle_summary_without_rescanning(client, settings, monkeypatch):
    directory = season(settings, 2)
    probes, _ = configure(monkeypatch)
    job_id = submit(client, directory, 'INSPECT')
    result = wait_batch(client, job_id)
    report = result['report']
    assert report['summary']['subtitleInspection']['withReference'] == 2
    assert report['summary']['subtitleInspection']['withPolish'] == 2
    report['summary'].pop('subtitleInspection')
    with client.app.state.jobs._connect() as db:
        db.execute('UPDATE jobs SET report_json=? WHERE id=?', (json.dumps(report), job_id))
    restored = client.get(f'/api/tasks/{job_id}').json()['report']['summary']['subtitleInspection']
    assert restored['total'] == restored['withPolish'] == restored['withReference'] == 2
    assert len(probes) == 2


def test_directory_retains_diagnostic_and_eligible_polish_materials(client, settings, monkeypatch, tmp_path):
    directory = season(settings, 2)
    configure(monkeypatch)
    source = next(directory.glob('*E01*.srt'))
    source.write_text('1\n00:04:21,000 --> 00:04:24,000\nNie znam tej drogi.\n')
    before = {path: path.read_bytes() for path in directory.iterdir()}
    job_id = submit(client, directory)
    body = wait_batch(client, job_id)
    assert body['status'] == 'WORKPACK_INCOMPLETE'
    assert all(item['state'] == 'ready' for item in body['report']['batch']['items'])
    result = client.get(f'/api/tasks/{job_id}/download')
    assert result.status_code == 200
    path = tmp_path / 'review-batch.zip'; path.write_bytes(result.content)
    validate_zip(path)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        entries = [item['manifest']['polish_candidates'][0] for item in manifest['items']]
        assert [entry['exportPurpose'] for entry in entries] == ['MANUAL_REVIEW', 'SYNCHRONIZATION']
        assert archive.read(entries[0]['archiveName']) == source.read_bytes()
        for item in manifest['items']:
            child = item['manifest']
            for artifact in child['reference']['files'] + child['polish_candidates']:
                name = artifact.get('archiveName') or artifact['name']
                assert hashlib.sha256(archive.read(name)).hexdigest() == artifact['sha256']
    assert before == {path: path.read_bytes() for path in directory.iterdir()}
