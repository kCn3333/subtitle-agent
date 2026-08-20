import io
import json
import time
import zipfile

import pytest

from app.models.media import MediaKind
from app.services.media_analysis import discover_external_subtitles, match_media_identity, parse_media_identity


@pytest.mark.parametrize(("name", "season", "episode", "episode_end"), [
    ("Lost.S01E24.mkv", 1, 24, None),
    ("Lost.s1e24.1080p.mkv", 1, 24, None),
    ("Lost.1x24.mkv", 1, 24, None),
    ("Lost.S01E23E24.mkv", 1, 23, 24),
    ("Lost.S01E23-E24.mkv", 1, 23, 24),
])
def test_episode_patterns(name, season, episode, episode_end):
    identity = parse_media_identity(name)
    assert identity.kind == MediaKind.EPISODE
    assert identity.series_title == "lost"
    assert (identity.season, identity.episode, identity.episode_end) == (season, episode, episode_end)
    assert identity.normalized_title == "lost"


def test_explicit_episode_conflict_is_rejected_even_for_same_series():
    media = parse_media_identity("Lost.S01E23.mkv")
    for candidate_name in ("Lost.S01E21.pl.srt", "Lost.S01E22.pl.srt", "Lost.S01E24.pl.srt",
                           "Lost.S02E23.pl.srt", "Lost.S01E23E24.pl.srt"):
        match = match_media_identity(media, parse_media_identity(candidate_name))
        assert match.accepted is False
        assert match.automatic is False
        assert match.confidence == 0
        assert any("sprzeczny" in reason for reason in match.reasons)


def test_same_episode_is_automatic_with_reasons():
    match = match_media_identity(parse_media_identity("Lost.S01E23.mkv"),
                                 parse_media_identity("Lost.s1e23.pol.0.srt"))
    assert match.accepted and match.automatic and match.confidence == 1
    assert any("S01E23" in reason for reason in match.reasons)


def test_missing_subtitle_episode_is_ambiguous_not_automatic():
    match = match_media_identity(parse_media_identity("Lost.S01E23.mkv"), parse_media_identity("Lost.pl.srt"))
    assert match.accepted is True
    assert match.automatic is False
    assert 0 < match.confidence < 1
    assert any("niejednoznaczne" in reason for reason in match.reasons)


def test_missing_episode_with_different_title_is_rejected():
    match = match_media_identity(parse_media_identity("Lost.S01E23.mkv"), parse_media_identity("Fringe.pl.srt"))
    assert match.accepted is False


def test_movie_title_and_optional_year_matching():
    movie = parse_media_identity("Mishima.1985.1080p.BluRay.mkv")
    exact = match_media_identity(movie, parse_media_identity("Mishima.1985.pl.srt"))
    without_year = match_media_identity(movie, parse_media_identity("Mishima.pl.srt"))
    wrong_year = match_media_identity(movie, parse_media_identity("Mishima.2008.pl.srt"))
    assert movie.kind == MediaKind.MOVIE and movie.year == 1985 and movie.normalized_title == "mishima"
    assert exact.automatic and exact.confidence == 1
    assert without_year.automatic and without_year.confidence < 1
    assert wrong_year.accepted is False


def test_discovery_exposes_confidence_reasons_and_omits_conflicts(tmp_path):
    media = tmp_path / "Lost.S01E23.mkv"; media.write_bytes(b"media")
    for name in ("Lost.S01E21.pl.srt", "Lost.S01E23.pl.srt", "Lost.S01E24.pl.srt", "Lost.pl.srt"):
        (tmp_path / name).write_text("1\n00:00:01,000 --> 00:00:02,000\nTekst\n")
    found = discover_external_subtitles(media)
    assert {item["name"] for item in found} == {"Lost.S01E23.pl.srt", "Lost.pl.srt"}
    exact = next(item for item in found if "S01E23" in item["name"])
    ambiguous = next(item for item in found if item["name"] == "Lost.pl.srt")
    assert exact["matchConfidence"] == 1 and exact["matchAutomatic"] is True and exact["matchReasons"]
    assert ambiguous["matchConfidence"] < 1 and ambiguous["matchAutomatic"] is False


def test_lost_directory_report_and_zip_never_mix_neighboring_episodes(client, settings):
    directory = settings.media_roots[0]
    for episode in range(21, 25):
        (directory / f"Lost.S01E{episode:02d}.mkv").write_bytes(f"episode-{episode}".encode())
        for version in range(2):
            (directory / f"Lost.S01E{episode:02d}.pol.{version}.srt").write_text(
                f"1\n00:00:01,000 --> 00:00:02,000\nOdcinek {episode}, wersja {version}\n")
    (directory / "Lost.pl.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nBez numeru\n")
    target = directory / "Lost.S01E23.mkv"
    response = client.post("/api/workpacks", json={"mediaPath": str(target), "taskType": "LANGUAGE_REVIEW"})
    assert response.status_code == 202
    job_id = response.json()["jobId"]
    for _ in range(200):
        body = client.get(f"/api/workpacks/{job_id}").json()
        if body["status"] in {"WORKPACK_READY", "WORKPACK_INCOMPLETE", "FAILED"}:
            break
        time.sleep(.01)
    assert body["status"] == "WORKPACK_INCOMPLETE"
    report = body["report"]
    discovered_names = {item["name"] for item in report["externalSubtitles"]}
    assert discovered_names == {"Lost.S01E23.pol.0.srt", "Lost.S01E23.pol.1.srt", "Lost.pl.srt"}
    included_names = {item["originalName"] for item in report["polishCandidates"]}
    assert included_names == {"Lost.S01E23.pol.0.srt", "Lost.S01E23.pol.1.srt"}
    archive_response = client.get(f"/api/workpacks/{job_id}/download")
    assert archive_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        packed_originals = {item["originalName"] for item in manifest["polish_candidates"]}
        assert packed_originals == included_names
        assert all(item["matchConfidence"] == 1 and item["matchReasons"] and item["matchAutomatic"]
                   for item in manifest["polish_candidates"])
        payload = b"".join(archive.read(name) for name in archive.namelist())
    assert all(marker not in payload for marker in (b"E21", b"E22", b"E24", b"Odcinek 21", b"Odcinek 22", b"Odcinek 24"))
