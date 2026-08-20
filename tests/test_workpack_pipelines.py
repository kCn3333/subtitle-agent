import time
import zipfile


def _wait(client, job_id: str) -> dict:
    for _ in range(300):
        body = client.get(f"/api/workpacks/{job_id}").json()
        if body["status"] in {"WORKPACK_READY", "WORKPACK_INCOMPLETE", "FAILED"}:
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
        return [output]

    monkeypatch.setattr("app.services.job_manager.extract_embedded", extract)


def test_inspection_v2_does_not_copy_materials_and_keeps_rejections(client, settings):
    media = settings.media_roots[0] / "Lost.S01E23.mkv"
    media.write_bytes(b"media")
    (media.parent / "Lost.S01E23.pl.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nTekst\n")
    (media.parent / "Lost.S01E24.pl.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nInny\n")
    response = client.post("/api/workpacks", json={"mediaPath": str(media), "taskType": "INSPECT"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "WORKPACK_READY"
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
    assert {item["name"] for item in report["rejectedSubtitleCandidates"]} == {"Lost.S01E24.pl.srt"}
    assert {item["name"] for item in report["rejectedPolishCandidates"]} == {"Lost.S01E24.pl.srt"}
    assert report["polishCandidates"] == [] and report["incompleteReasons"] == []
    with zipfile.ZipFile(report["workpack"]["path"]) as archive:
        assert not any(name.startswith(("polish/", "reference/")) for name in archive.namelist())


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


def test_translation_requires_text_english_but_not_polish(client, media_file, monkeypatch):
    _configure_probe(monkeypatch); _configure_extraction(monkeypatch)
    response = client.post("/api/workpacks", json={"mediaPath": str(media_file), "taskType": "PREPARE_TRANSLATION"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "WORKPACK_READY"
    assert body["report"]["pipeline"] == "PREPARE_TRANSLATION"
    assert body["report"]["polishCandidates"] == [] and body["report"]["incompleteReasons"] == []


def test_translation_rejects_graphic_english_reference(client, media_file, monkeypatch):
    _configure_probe(monkeypatch, "graphic")
    response = client.post("/api/workpacks", json={"mediaPath": str(media_file), "taskType": "PREPARE_TRANSLATION"})
    body = _wait(client, response.json()["jobId"])
    assert body["status"] == "WORKPACK_INCOMPLETE"
    assert "Brak wymaganej tekstowej referencji angielskiej" in body["report"]["incompleteReasons"]
