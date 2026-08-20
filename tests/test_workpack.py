import hashlib
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.media_analysis import rank_english
from app.services.system_probe import ToolInfo
from app.services.workpack import (build_zip, copy_polish_candidates, extract_embedded, graphic_timeline,
                                   media_summary, safe_archive_name, safe_filename, sha256_file, timeline)


def srt(text="Zażółć gęślą jaźń") -> bytes:
    return f"1\r\n00:00:01,000 --> 00:00:02,500\r\n{text}\r\n".encode("cp1250")


def test_default_mode_is_workpack_and_needs_no_key():
    settings = Settings(_env_file=None)
    assert settings.subtitle_agent_app_mode == "WORKPACK"
    assert settings.openai_configured is False


def test_workpack_ignores_stale_key_file(tmp_path):
    settings=Settings(subtitle_agent_app_mode='WORKPACK',openai_api_key='secret',
                      openai_api_key_file=tmp_path/'missing-secret')
    assert settings.openai_configured is False


def test_mode_validation_and_limits():
    assert Settings(subtitle_agent_app_mode="advanced").subtitle_agent_app_mode == "ADVANCED"
    with pytest.raises(ValueError): Settings(subtitle_agent_app_mode="unsafe")
    with pytest.raises(ValueError): Settings(workpack_max_files=0)


def test_ranking_prefers_full_dialogue_and_penalizes_partial_tracks():
    tracks = [
        {"streamIndex": 1, "language": "eng", "title": "Japanese Parts Only", "type": "text", "default": True},
        {"streamIndex": 2, "language": "eng", "title": "Full Dialogue", "type": "text", "default": False},
        {"streamIndex": 3, "language": "eng", "title": "Director Commentary", "type": "text", "default": False},
        {"streamIndex": 4, "language": "eng", "title": "Forced", "type": "text", "forced": True},
    ]
    ranked = rank_english(tracks, [])
    assert ranked[0]["streamIndex"] == 2
    assert next(x for x in ranked if x["streamIndex"] == 3)["score"] < ranked[0]["score"]
    assert next(x for x in ranked if x["streamIndex"] == 4)["score"] < ranked[0]["score"]


def test_ambiguity_can_be_calculated_from_margin():
    ranked = rank_english([
        {"streamIndex": 1, "language": "eng", "title": "English A", "type": "text"},
        {"streamIndex": 2, "language": "eng", "title": "English B", "type": "text"}], [])
    assert ranked[0]["score"] - ranked[1]["score"] < 10


@pytest.mark.parametrize("unsafe", ["../secret", "/absolute", "safe/../../secret"])
def test_archive_path_rejects_traversal(unsafe):
    with pytest.raises(ValueError): safe_archive_name(unsafe)


def test_archive_names_are_sanitized():
    assert safe_filename("../../Film:<bad>.mkv") == "Film_bad_.mkv"
    assert safe_archive_name("polish/candidate-001.pl.srt") == "polish/candidate-001.pl.srt"


def test_polish_copy_is_byte_for_byte_and_keeps_cp1250(tmp_path):
    source = tmp_path / "Movie.pol.0.srt"; raw = srt(); source.write_bytes(raw)
    ranking = [{"sourceType": "external", "path": str(source), "name": source.name, "format": "srt",
                "languageHint": "pl", "score": 90, "analysis": {"encoding": "cp1250"}}]
    included, omitted = copy_polish_candidates(ranking, tmp_path / "job" / "polish", 10)
    copied = tmp_path / "job" / included[0]["archiveName"]
    assert copied.read_bytes() == raw
    assert included[0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert omitted == []


def test_polish_limit_and_symlink_rejection(tmp_path):
    ranking=[]
    for number in range(3):
        path=tmp_path/f"Movie.pol.{number}.srt"; path.write_bytes(srt(str(number)))
        ranking.append({"sourceType":"external","path":str(path),"name":path.name,"format":"srt","languageHint":"pl","score":90-number})
    included, omitted = copy_polish_candidates(ranking, tmp_path/"job"/"polish", 2)
    assert len(included) == 2 and len(omitted) == 1
    outside=tmp_path/"outside.srt"; outside.write_bytes(srt()); link=tmp_path/"link.srt"; link.symlink_to(outside)
    linked=[{"sourceType":"external","path":str(link),"name":link.name,"languageHint":"pl","score":99}]
    assert copy_polish_candidates(linked, tmp_path/"other"/"polish", 10)[0] == []


def test_timeline_contains_hash_not_text(tmp_path):
    path=tmp_path/"one.srt"; path.write_bytes(srt())
    result=timeline(path,"candidate")
    assert result["cue_count"] == 1 and result["cues"][0]["start_ms"] == 1000
    serialized=json.dumps(result, ensure_ascii=False)
    assert "Zażółć" not in serialized and len(result["cues"][0]["normalized_text_sha256"]) == 64


@pytest.mark.anyio
async def test_text_ass_is_kept_and_converted_to_srt(tmp_path, monkeypatch):
    media=tmp_path/'movie.mkv'; media.write_bytes(b'x')
    async def fake(arguments, timeout):
        Path(arguments[-1]).write_bytes(b'converted')
    monkeypatch.setattr('app.services.workpack.run_process', fake)
    outputs=await extract_embedded({'streamIndex':3,'codec':'ass','type':'text'},media,tmp_path/'reference',1)
    assert {path.name for path in outputs} == {'selected.original.ass','selected.eng.srt'}


@pytest.mark.anyio
async def test_pgs_sup_and_timeline(tmp_path, monkeypatch):
    media=tmp_path/'movie.mkv'; media.write_bytes(b'x')
    async def fake(arguments, timeout):
        if arguments[0] == 'ffmpeg': Path(arguments[-1]).write_bytes(b'pgs')
        return type('Result',(),{'stdout':json.dumps({'packets':[{'pts_time':'1.25','duration_time':'0.5','stream_index':4}]})})()
    monkeypatch.setattr('app.services.workpack.run_process', fake)
    outputs=await extract_embedded({'streamIndex':4,'codec':'hdmv_pgs_subtitle','type':'graphic'},media,tmp_path/'reference',1)
    events=await graphic_timeline(media,4,1)
    assert outputs[0].name == 'selected.eng.sup'
    assert events['events'][0] == {'sequence':1,'start_ms':1250,'end_ms':1750,'duration_ms':500,'stream_index':4}


@pytest.mark.anyio
async def test_dvd_reference_requires_idx_and_sub(tmp_path, monkeypatch):
    media=tmp_path/'movie.mkv'; media.write_bytes(b'x')
    async def fake(arguments, timeout):
        output=Path(arguments[-1]); output.write_bytes(b'idx'); output.with_suffix('.sub').write_bytes(b'sub')
    monkeypatch.setattr('app.services.workpack.run_process', fake)
    outputs=await extract_embedded({'streamIndex':5,'codec':'dvd_subtitle','type':'graphic'},media,tmp_path/'reference',1)
    assert {path.suffix for path in outputs} == {'.idx','.sub'}


def test_media_summary_drops_full_path_and_has_exact_fps():
    summary=media_summary({"path":"/private/secret/Movie.mkv","name":"Movie.mkv","durationSeconds":2.5,
                           "avgFrameRate":"24000/1001","audioTracks":[]})
    assert "path" not in summary and summary["duration_ms"] == 2500
    assert summary["fps"]["fraction"] == "24000/1001"


def test_zip_checksums_order_security_and_no_media(tmp_path):
    job=tmp_path/"job"; (job/"analysis").mkdir(parents=True); (job/"polish").mkdir()
    (job/"manifest.json").write_text('{}'); (job/"REQUEST.md").write_text('request')
    (job/"README.txt").write_text('readme'); (job/"analysis"/"x.json").write_text('{}')
    (job/"polish"/"candidate-001.pl.srt").write_bytes(srt())
    archive, version, digest, omitted=build_zip(job,"Movie",1000000,100)
    assert version == 1 and digest == sha256_file(archive) and omitted == []
    with zipfile.ZipFile(archive) as bundle:
        names=bundle.namelist(); assert names == sorted(names)
        assert all(not name.startswith('/') and '..' not in Path(name).parts for name in names)
        assert not any(name.endswith('.mkv') for name in names)
        sums=bundle.read('checksums.sha256').decode().splitlines()
        assert sums == sorted(sums, key=lambda line: line.split('  ',1)[1])
        for line in sums:
            digest_value,name=line.split('  ',1); assert hashlib.sha256(bundle.read(name)).hexdigest()==digest_value


def test_zip_file_limit_marks_omissions(tmp_path):
    for number in range(5): (tmp_path/f"{number}.txt").write_text(str(number))
    archive, _, _, omitted=build_zip(tmp_path,"Movie",1000000,3)
    assert archive.is_file() and len(omitted) == 3


def test_workpack_mode_blocks_advanced_endpoints(tmp_path, monkeypatch):
    media=tmp_path/"media"; media.mkdir(); settings=Settings(data_root=tmp_path/"data",media_roots=[media])
    monkeypatch.setattr("app.main.probe_tools",lambda:ToolInfo("ffmpeg test","ffprobe test"))
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/jobs/semantic/config').status_code == 404
        assert client.get('/api/jobs/publishing/config').status_code == 404
        assert client.post('/api/jobs/missing/alignment',json={}).status_code == 404


def test_workpack_api_persists_download_and_does_not_modify_media(client, media_file, settings):
    subtitle = media_file.with_name(f"{media_file.stem}.pl.srt"); original = srt(); subtitle.write_bytes(original)
    before = {path.name: path.read_bytes() for path in media_file.parent.iterdir() if path.is_file()}
    response = client.post('/api/workpacks', json={"mediaPath": str(media_file),
                                                   "taskType": "SYNC_AND_LANGUAGE_REVIEW"})
    assert response.status_code == 202; job_id = response.json()["jobId"]
    for _ in range(200):
        report_response = client.get(f'/api/workpacks/{job_id}'); body = report_response.json()
        if body["status"] in {"WORKPACK_READY", "WORKPACK_INCOMPLETE", "FAILED"}: break
        time.sleep(.01)
    assert body["status"] == "WORKPACK_INCOMPLETE"
    downloaded = client.get(f'/api/workpacks/{job_id}/download')
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == downloaded.headers['x-workpack-sha256']
    assert subtitle.read_bytes() == original
    assert before == {path.name: path.read_bytes() for path in media_file.parent.iterdir() if path.is_file()}
    from app.services.job_manager import JobManager
    restarted = JobManager(settings.data_root / 'subtitle-agent.db', settings)
    assert restarted.get(job_id)['report']['workpack']['sha256'] == downloaded.headers['x-workpack-sha256']


def test_download_rejects_recorded_path_outside_job(client, settings):
    manager=client.app.state.jobs; job_id='unsafe'; outside=settings.data_root/'outside.zip'; outside.write_bytes(b'x')
    settings.data_root.mkdir(parents=True,exist_ok=True)
    with manager._connect() as db:
        db.execute("""INSERT INTO jobs (id,media_path,status,progress,created_at,finished_at,report_json,job_type,task_type)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (job_id,'/media/x.mkv','WORKPACK_READY',100,'2026-01-01','2026-01-01',
                    json.dumps({'workpack':{'path':str(outside),'sha256':sha256_file(outside),'filename':'outside.zip'}}),
                    'PREPARE_WORKPACK','SYNC_ONLY'))
    assert client.get(f'/api/workpacks/{job_id}/download').status_code == 404


def test_compose_has_no_openai_publish_or_rw_mount():
    compose=Path('compose.example.yml').read_text()
    assert 'SUBTITLE_AGENT_APP_MODE: WORKPACK' in compose
    assert 'OPENAI_API_KEY' not in compose and '/publish' not in compose and ':rw' not in compose
