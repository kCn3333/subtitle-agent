import hashlib
from pathlib import Path

import pytest

from app.services.alignment import (Anchor, Cue, FixtureAnchorProvider, StructuralAnchorProvider,
                                    fit_models, normalize_text, parse_cues, quality, select_model,
                                    transform, write_preview)


def anchors(offset=0, scale=1.0, count=12, outlier=False):
    result = [Anchor(i, i, round(scale * i * 10000 + offset), i * 10000, .9, "fixture") for i in range(count)]
    if outlier:
        result[5] = Anchor(5, 5, 999999, 50000, .2, "fixture")
    return result


def selected(points, **kwargs):
    return select_model(fit_models(points, **kwargs))


def test_identity_and_positive_and_negative_offset():
    assert selected(anchors())["strategy"] == "IDENTITY"
    assert selected(anchors(2300))["strategy"] == "GLOBAL_OFFSET"
    assert selected(anchors(-1800))["offsetMs"] == -1800


def test_affine_drift_and_fps_like_scale():
    model = selected(anchors(400, 1.02))
    assert model["strategy"] == "AFFINE_DRIFT"
    assert model["scale"] == pytest.approx(1.02)
    fps = selected(anchors(scale=25 / 23.976))
    assert fps["scale"] == pytest.approx(25 / 23.976, rel=1e-5)


def test_outlier_is_rejected_and_simple_model_wins():
    model = selected(anchors(1200, outlier=True))
    assert model["strategy"] == "GLOBAL_OFFSET"
    assert model["inlierCount"] == 11


def test_piecewise_for_single_cut_but_not_linear_data():
    points = anchors(count=16)
    cut = [Anchor(a.english_index, a.polish_index, a.reference_time + (3000 if i >= 8 else 0),
                  a.source_time, .9, "fixture") for i, a in enumerate(points)]
    assert selected(cut, max_segments=2)["strategy"] == "PIECEWISE_LINEAR"
    assert selected(anchors(scale=1.01), max_segments=3)["strategy"] != "PIECEWISE_LINEAR"


def test_low_count_and_coverage_are_unusable():
    model = selected(anchors(count=3))
    assert quality(model) == "UNUSABLE"


def test_structural_provider_does_not_guess_with_too_few_cues():
    cue = Cue("x", 1, 0, 1000, 1000, "Text", "text", "x")
    assert StructuralAnchorProvider().provide([cue] * 5, [cue] * 5, 10000, {}) == []


def test_transform_reports_negative_reversed_and_overlap():
    cues = [Cue("1", 1, 100, 200, 100, "A", "a", "pl"), Cue("2", 2, 210, 300, 90, "B", "b", "pl")]
    model = {"predict": lambda value: -value}
    _, report = transform(cues, model, 1000)
    assert report["negativeTimesBeforeClamp"] == 2
    assert report["reversedSegments"] == 2


def test_parser_and_writer_preserve_polish_text_and_input(tmp_path):
    source = tmp_path / "input.srt"
    original = "1\r\n00:00:01,000 --> 00:00:02,000\r\n<i>Żółw</i> — [MUZYKA]\r\n"
    source.write_bytes(original.encode("utf-8"))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    cues = parse_cues(source, "polish")
    assert cues[0].raw_text == "<i>Żółw</i> — [MUZYKA]"
    assert "muzyka" not in cues[0].normalized_text
    preview = tmp_path / "preview.AI-Sync.pl.srt"
    write_preview(cues, preview)
    assert preview.read_bytes().startswith(b"1\n00:00:01,000")
    assert b"\r" not in preview.read_bytes()
    assert "<i>Żółw</i> — [MUZYKA]" in preview.read_text()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not (tmp_path / ".preview.AI-Sync.pl.srt.tmp").exists()


def test_fixture_provider_is_controlled():
    fixture = anchors(count=4)
    assert FixtureAnchorProvider(fixture).provide([], [], 0, {}) == fixture


def test_normalization_does_not_modify_raw_value():
    raw = "<i>JOHN:</i> “Hello!”"
    assert "hello" in normalize_text(raw)
    assert "john" not in normalize_text(raw)
    assert raw == "<i>JOHN:</i> “Hello!”"
