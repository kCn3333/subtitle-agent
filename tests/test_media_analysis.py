from pathlib import Path

import pytest

from app.services.media_analysis import (
    UserInputError, discover_external_subtitles, extract_reference, parse_ffprobe,
    rank_english, rank_polish, validate_media_path,
)


def test_allowed_path_and_unsupported_extension(tmp_path):
    root = tmp_path / "media"; root.mkdir()
    movie = root / "movie.MKV"; movie.write_bytes(b"x")
    assert validate_media_path(str(movie), [root]) == movie.resolve()
    bad = root / "movie.exe"; bad.write_bytes(b"x")
    with pytest.raises(UserInputError, match="rozszerzenie"):
        validate_media_path(str(bad), [root])


def test_symlink_outside_root_is_rejected(tmp_path):
    root = tmp_path / "media"; root.mkdir()
    outside = tmp_path / "outside.mkv"; outside.write_bytes(b"x")
    link = root / "link.mkv"; link.symlink_to(outside)
    with pytest.raises(UserInputError, match="poza"):
        validate_media_path(str(link), [root])


def test_parse_ffprobe_handles_missing_metadata(tmp_path):
    movie = tmp_path / "movie.mkv"; movie.write_bytes(b"x")
    report = parse_ffprobe({"format": {"format_name": "matroska", "duration": "10.5"}, "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
         "r_frame_rate": "24/1", "avg_frame_rate": "24000/1001"},
        {"index": 2, "codec_type": "subtitle", "codec_name": "subrip"},
        {"index": 3, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "eng"}},
    ]}, movie)
    assert report["embeddedSubtitles"][0]["title"] is None
    assert [item["type"] for item in report["embeddedSubtitles"]] == ["text", "graphic"]


def test_english_full_dialogue_beats_commentary_and_forced():
    tracks = [
        {"streamIndex": 1, "language": "eng", "title": "English", "type": "text", "default": True, "forced": False, "hearingImpaired": False},
        {"streamIndex": 2, "language": "eng", "title": "Director Commentary", "type": "text", "default": False, "forced": False, "hearingImpaired": False},
        {"streamIndex": 3, "language": "eng", "title": "English forced", "type": "graphic", "default": False, "forced": True, "hearingImpaired": False},
    ]
    assert rank_english(tracks, [])[0]["streamIndex"] == 1


def test_polish_ranking_avoids_ai_sync():
    external = [
        {"name": "movie.pl.srt", "format": "srt", "languageHint": "pl", "aiSync": False, "analysis": {"segment_count": 500}},
        {"name": "movie.AI-Sync-v001.pl.srt", "format": "srt", "languageHint": "pl", "aiSync": True, "analysis": {"segment_count": 500}},
    ]
    ranked = rank_polish({"durationSeconds": 1000}, external, [])
    assert ranked[0]["name"] == "movie.pl.srt"
    assert next(item for item in ranked if item["aiSync"])["eligibleByDefault"] is False


def test_small_timing_overrun_is_not_classified_as_different_edition():
    external = [{
        "name": "movie.pl.srt", "format": "srt", "languageHint": "pl",
        "identityMatch": {"status": "MATCH", "confidence": .9, "automatic": True, "reasons": []},
        "analysis": {"segment_count": 100, "first_time": 1.0, "last_time": 102.5},
    }]
    candidate = rank_polish({"durationSeconds": 100.0}, external, [])[0]
    assert candidate["timingCompatibility"] == "COMPATIBLE"
    assert candidate["reasonCode"] is None
    assert candidate["eligibleByDefault"] is True


def test_external_discovery_is_non_recursive_and_matches_release_stem(tmp_path):
    movie = tmp_path / "Mishima.1985.1080p.mkv"; movie.write_bytes(b"x")
    (tmp_path / "Mishima.pl.srt").write_text("00:00:01,000 --> 00:00:02,000\nTest")
    (tmp_path / "Other.pl.srt").write_text("ignored")
    nested = tmp_path / "nested"; nested.mkdir()
    (nested / "Mishima.eng.srt").write_text("ignored")
    found = discover_external_subtitles(movie)
    assert [item["name"] for item in found] == ["Mishima.pl.srt"]


def test_release_stem_with_parenthesized_year_matches(tmp_path):
    movie = tmp_path / "Mishima - A Life in Four Chapters (1985) [Bluray-1080p].mkv"
    movie.write_bytes(b"x")
    subtitle = tmp_path / "Mishima - A Life in Four Chapters.pl.srt"
    subtitle.write_text("00:00:01,000 --> 00:00:02,000\nTest")
    assert discover_external_subtitles(movie)[0]["name"] == subtitle.name


def test_external_subtitle_symlink_outside_directory_is_ignored(tmp_path):
    directory = tmp_path / "media"; directory.mkdir()
    movie = directory / "Movie.mkv"; movie.write_bytes(b"x")
    outside = tmp_path / "Movie.pl.srt"; outside.write_text("00:00:01,000 --> 00:00:02,000\nTest")
    (directory / "Movie.pl.srt").symlink_to(outside)
    assert discover_external_subtitles(movie) == []


@pytest.mark.anyio
async def test_reference_extraction_uses_safe_stream_index_name(tmp_path, monkeypatch):
    movie = tmp_path / "movie.mkv"; movie.write_bytes(b"media")
    work = tmp_path / "work"; work.mkdir()
    captured = []
    async def fake_run(arguments, timeout):
        captured.extend(arguments)
        Path(arguments[-1]).write_bytes(b"subtitle")
    monkeypatch.setattr("app.services.subtitle_extraction.run_process", fake_run)
    output = await extract_reference(
        {"sourceType": "embedded", "streamIndex": 7, "type": "text", "codec": "ass", "title": "../../unsafe"},
        movie, work, 1,
    )
    assert Path(output).name == "reference-stream-7.srt"
    assert captured[0] == "ffmpeg"
