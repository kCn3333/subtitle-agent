import json
import time
import zipfile

from app.services.subtitle_extraction import SubtitleExtractionResult
from app.services.process_runner import ProcessExecutionError


def _wait(client, job_id: str) -> dict:
    for _ in range(300):
        body = client.get(f"/api/workpacks/{job_id}").json()
        if body["status"] in {"INSPECTION_READY", "WORKPACK_READY", "WORKPACK_INCOMPLETE", "NEEDS_OCR", "FAILED"}:
            return body
        time.sleep(.01)
    raise AssertionError("pipeline did not finish")


def _embedded(subtitle_type: str = "text") -> dict:
    return {"streamIndex": 2, "subtitleOrder": 0, "codec": "subrip" if subtitle_type == "text" else "hdmv_pgs_subtitle",
            "language": "eng", "title": "English Full Dialogue", "default": True, "forced": False,
            "hearingImpaired": False, "type": subtitle_type}


def _configure_probe(monkeypatch, subtitle_type: str = "text") -> None:
    async def probe(path, timeout):
        return {"path": str(path), "name": path.name, "sizeBytes": path.stat().st_size,
                "container": "matroska", "durationSeconds": 100.0, "width": 1920, "height": 1080,
                "avgFrameRate": "24000/1001", "rFrameRate": "24000/1001", "videoCodec": "h264",
                "audioTracks": [], "embeddedSubtitles": [_embedded(subtitle_type)]}

    monkeypatch.setattr("app.services.job_manager.probe_media", probe)


def _configure_extraction(monkeypatch) -> None:
    async def extract(reference, media_path, target, timeout):
        target.mkdir(parents=True, exist_ok=True)
        output = target / "selected.eng.srt"
        output.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        return SubtitleExtractionResult([output], [])

    monkeypatch.setattr("app.services.job_manager.extract_embedded", extract)


def test_inspection_v2_does_not_copy_materials_and_keeps_rejections(client, settings):
    media = settings.media_roots[0] / "Lost.S01E23.mkv"
    media.write_bytes(b"media")
    (media.parent / "Lost.S01E23.pl.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nTekst\n")
    (media.parent / "Lost.S01E24.pl.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nInny\n")
    response = client.post("/api/workpacks", json={"mediaPath": str(media), "taskType": "INSPECT"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "INSPECTION_READY"
    report = body["report"]
    assert report["reportVersion"] == 2 and report["pipeline"] == "INSPECT"
    assert report["media"]["identity"]["kind"] == "EPISODE"
    assert report["mediaInspection"]["fps"]["fraction"] == "24/1"
    assert report["mediaInspection"]["container"] == "matroska"
    assert report["mediaInspection"]["video_codec"] == "h264"
    assert (report["mediaInspection"]["width"], report["mediaInspection"]["height"]) == (1920, 1080)
    assert report["embeddedSubtitleTracks"] == []
    assert report["polishCandidateInspection"][0]["segments"] == 1
    assert report["polishCandidateInspection"][0]["structuralErrors"]["malformedSegments"] == 0
    assert report["rejectedSubtitleCandidates"] == [] and report["rejectedPolishCandidates"] == []
    assert report["ignoredUnrelatedSubtitleFiles"] == 1
    assert report["polishCandidates"] == [] and report["incompleteReasons"] == []
    assert report["workpack"] is None
    assert not (settings.data_root / "work" / "jobs" / body["jobId"]).exists()


def test_sync_requires_english_and_a_valid_polish_candidate(client, media_file):
    response = client.post("/api/workpacks", json={"mediaPath": str(media_file), "taskType": "PREPARE_SYNC"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "WORKPACK_INCOMPLETE"
    assert set(body["report"]["incompleteReasons"]) == {
        "Brak wymaganej angielskiej referencji", "Brak prawidłowo dopasowanego kandydata polskiego"}


def test_sync_is_ready_with_english_and_matched_polish(client, media_file, monkeypatch):
    _configure_probe(monkeypatch); _configure_extraction(monkeypatch)
    media_file.with_name("Example Movie.pl.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nTekst\n")
    response = client.post("/api/workpacks", json={"mediaPath": str(media_file), "taskType": "PREPARE_SYNC"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "WORKPACK_READY"
    assert body["report"]["pipeline"] == "PREPARE_SYNC"
    assert body["report"]["incompleteReasons"] == []
    hypothesis = body["report"]["synchronizationHypotheses"][0]
    assert {"englishSegments", "polishSegments", "hypothesis", "offsetMs", "spreadMs",
            "analysisCoverage", "confidence", "verification", "sufficientAnchors"} <= hypothesis.keys()
    assert hypothesis["sufficientAnchors"] is False
    with zipfile.ZipFile(body["report"]["workpack"]["path"]) as archive:
        names = set(archive.namelist())
        assert names == {"manifest.json", "REQUEST.md", "reference/selected/selected.eng.srt",
                         "polish/candidate-001.pl.srt", "analysis/inspection-report.json",
                         "analysis/media-summary.json", "analysis/subtitle-streams.json",
                         "analysis/timing-comparison.json", "checksums.sha256"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == "subtitle-workpack-v2"
        assert manifest["expected_output"]["preserve_timing"] is False
        assert manifest["expected_output"]["preserve_text"] is True
        assert "AI-Synced" in manifest["expected_output"]["filename"]
        timing = json.loads(archive.read("analysis/timing-comparison.json"))
        assert timing["readySynchronization"] is False
        inspection = json.loads(archive.read("analysis/inspection-report.json"))
        assert {"mediaIdentity", "media", "embeddedSubtitleTracks", "englishRanking",
                "polishCandidates", "rejectedPolishCandidates", "synchronizationHypotheses"} <= inspection.keys()


def test_same_episode_title_from_different_production_is_blocked(client, settings, monkeypatch):
    media = settings.media_roots[0] / "Scenes From A Marriage - S01E01 - Innocence and Panic Bluray-1080p.mkv"
    media.write_bytes(b"media")
    subtitle = media.with_name("Scenes From A Marriage - S01E01 - Innocence and Panic Bluray-1080p.pol.srt")
    cue_starts = [round(16_040 + index * (3_351_289 - 16_040) / 622) for index in range(623)]
    subtitle.write_text("\n".join(
        f"{number}\n{start // 3_600_000:02d}:{start // 60_000 % 60:02d}:{start // 1_000 % 60:02d},{start % 1_000:03d} --> "
        f"{(start + 1_000) // 3_600_000:02d}:{(start + 1_000) // 60_000 % 60:02d}:"
        f"{(start + 1_000) // 1_000 % 60:02d},{(start + 1_000) % 1_000:03d}\nTekst {number}\n"
        for number, start in enumerate(cue_starts, 1)
    ), encoding="utf-8")

    async def probe(path, timeout):
        reference = _embedded("graphic")
        reference["codec"] = "dvd_subtitle"
        return {"path": str(path), "name": path.name, "sizeBytes": path.stat().st_size,
                "container": "matroska", "durationSeconds": 3130.388, "width": 1440, "height": 1080,
                "avgFrameRate": "24000/1001", "rFrameRate": "24000/1001", "videoCodec": "hevc",
                "audioTracks": [{"language": "swe"}], "embeddedSubtitles": [reference]}

    async def extract(reference, media_path, target, timeout):
        target.mkdir(parents=True, exist_ok=True)
        index_path = target / "selected.eng.idx"
        timestamps = [round(46_463 + index * (3_057_388 - 46_463) / 759) for index in range(760)]
        index_path.write_text("# VobSub index file, v7\nid: en, index: 0\n" + "\n".join(
            f"timestamp: {value // 3_600_000:02d}:{value // 60_000 % 60:02d}:"
            f"{value // 1_000 % 60:02d}:{value % 1_000:03d}, filepos: {number:09X}"
            for number, value in enumerate(timestamps)
        ), encoding="utf-8")
        sub_path = target / "selected.eng.sub"
        sub_path.write_bytes(b"vobsub")
        return SubtitleExtractionResult([index_path, sub_path], [])

    monkeypatch.setattr("app.services.job_manager.probe_media", probe)
    monkeypatch.setattr("app.services.job_manager.extract_embedded", extract)
    response = client.post("/api/workpacks", json={"mediaPath": str(media), "taskType": "PREPARE_SYNC"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "WORKPACK_INCOMPLETE"
    assert body["report"]["polishCandidates"] == []
    suspect = body["report"]["incompatiblePolishCandidates"][0]
    assert suspect["timingCompatibility"] == "INCOMPATIBLE"
    assert suspect["reasonCode"] == "LIKELY_DIFFERENT_EDITION"
    assert suspect["eligibleByDefault"] is False
    assert body["report"]["incompleteReasons"] == [
        "Znaleziony polski plik prawdopodobnie pochodzi z innej wersji lub produkcji. "
        "Automatyczna synchronizacja została zablokowana."
    ]
    assert body["report"]["synchronizationHypotheses"][0]["structuralOnly"] is True
    with zipfile.ZipFile(body["report"]["workpack"]["path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["reference"]["cueCount"] == 760
        assert manifest["reference"]["firstMs"] == 46_463
        assert manifest["reference"]["lastMs"] == 3_057_388
        assert manifest["polish_candidates"] == []
        assert manifest["expected_output"]["preserve_timing"] is False
        assert manifest["expected_output"]["preserve_text"] is True
        assert "AI-Synced" in manifest["expected_output"]["filename"]
        assert not any(name.startswith("polish/") for name in archive.namelist())


def test_workpack_process_error_is_visible_in_failed_event(client, media_file, monkeypatch):
    _configure_probe(monkeypatch)

    async def fail(*args, **kwargs):
        raise ProcessExecutionError("Nie udało się wyeksportować ścieżki DVD/VobSub. kod 2: test")

    monkeypatch.setattr("app.services.job_manager.extract_embedded", fail)
    created = client.post("/api/tasks", json={"mediaPath": str(media_file), "mode": "PREPARE_SYNC"})
    body = _wait(client, created.json()["jobId"])
    assert body["status"] == "FAILED"
    assert "DVD/VobSub" in body["errorMessage"]
    assert "DVD/VobSub" in client.app.state.jobs.events(body["jobId"])[-1].message


def test_polish_subtitle_overrun_is_reported(client, media_file):
    media_file.with_name("Example Movie.pl.srt").write_text(
        "1\n00:01:45,000 --> 00:01:46,000\nTekst\n", encoding="utf-8"
    )
    created = client.post("/api/tasks", json={"mediaPath": str(media_file), "mode": "INSPECT"})
    body = _wait(client, created.json()["jobId"])
    assert any("00:00:06.000 po zakończeniu materiału" in warning for warning in body["report"]["warnings"])
    assert body["report"]["polishCandidateInspection"][0]["endOverrunSeconds"] == 6


def test_translation_requires_text_english_but_not_polish(client, media_file, monkeypatch):
    _configure_probe(monkeypatch); _configure_extraction(monkeypatch)
    response = client.post("/api/workpacks", json={"mediaPath": str(media_file), "taskType": "PREPARE_TRANSLATION"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "WORKPACK_READY"
    assert body["report"]["pipeline"] == "PREPARE_TRANSLATION"
    assert body["report"]["polishCandidates"] == [] and body["report"]["incompleteReasons"] == []
    with zipfile.ZipFile(body["report"]["workpack"]["path"]) as archive:
        assert set(archive.namelist()) == {"manifest.json", "REQUEST.md",
            "reference/selected/selected.eng.srt", "analysis/media-summary.json",
            "analysis/subtitle-streams.json", "checksums.sha256"}


def test_translation_rejects_graphic_english_reference(client, media_file, monkeypatch):
    _configure_probe(monkeypatch, "graphic")
    response = client.post("/api/workpacks", json={"mediaPath": str(media_file), "taskType": "PREPARE_TRANSLATION"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "NEEDS_OCR"
    assert "Brak wymaganej tekstowej referencji angielskiej" in body["report"]["incompleteReasons"]
    assert body["report"]["workpack"] is None


def test_graphic_inspection_uses_timeline_without_export_and_leaves_no_directory(client, media_file, settings, monkeypatch):
    _configure_probe(monkeypatch, "graphic")
    async def forbidden_extract(*args, **kwargs):
        raise AssertionError("INSPECT must not export PGS/SUP")
    async def fake_timeline(*args, **kwargs):
        return {"event_count": 1, "events": [{"start_ms": 1000}]}
    monkeypatch.setattr("app.services.job_manager.extract_embedded", forbidden_extract)
    monkeypatch.setattr("app.services.job_manager.graphic_timeline", fake_timeline)
    created = client.post("/api/tasks", json={"mediaPath": str(media_file), "mode": "INSPECT"})
    body = _wait(client, created.json()["jobId"])
    assert body["status"] == "INSPECTION_READY" and body["report"]["workpack"] is None
    assert not (settings.data_root / "work" / "jobs" / body["jobId"]).exists()


def test_unified_tasks_api_accepts_mode_and_legacy_endpoint_remains(client, media_file):
    created = client.post("/api/tasks", json={"mediaPath": str(media_file), "mode": "INSPECT"})
    assert created.status_code == 202 and created.json()["mode"] == "INSPECT"
    body = _wait(client, created.json()["jobId"])
    assert body["report"]["pipeline"] == "INSPECT"
    assert client.get(f"/api/tasks/{created.json()['jobId']}").status_code == 200
    legacy = client.post("/api/workpacks", json={"mediaPath": str(media_file), "taskType": "INSPECT"})
    assert legacy.status_code == 202


def test_gui_exposes_only_three_polish_modes_and_config_is_v2(client):
    html = client.get("/").text
    assert all(label in html for label in ("Sprawdź napisy", "Przygotuj do synchronizacji",
                                           "Przygotuj do tłumaczenia"))
    assert "SYNC_AND_LANGUAGE_REVIEW" not in html and "INSPECT_SUBTITLES" not in html
    assert client.get("/api/workpacks/config").json()["schemaVersion"] == "subtitle-workpack-v2"
