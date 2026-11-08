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


@pytest.mark.parametrize('scale', [24000 / 1001 / 25, 24 / 25, 25 / (24000 / 1001), 25 / 24])
def test_standard_fps_requires_independent_dialogues_across_full_duration(scale):
    points = anchors(900, scale, count=24)
    models = fit_models(points, duration_ms=round(scale * 230000 + 900))
    fps = next(m for m in models if m['strategy'] == 'STANDARD_FPS' and m['scale'] == scale)
    assert fps['dialogueVerified'] is True
    assert quality(fps) == 'HIGH'
    assert select_model(models)['strategy'] == 'STANDARD_FPS'
    assert fps['offsetMs'] == 900
    for unconfirmed in (
        [Anchor(a.english_index, a.polish_index, a.reference_time, a.source_time, .95, 'structural') for a in points],
        points[:8], points[8:16], points[-8:],
        [Anchor(0, a.polish_index, a.reference_time, a.source_time, .95, 'semantic') for a in points],
    ):
        unconfirmed_models = fit_models(unconfirmed, duration_ms=round(scale * 230000 + 900))
        assert all(quality(m) not in {'HIGH', 'MEDIUM'} for m in unconfirmed_models if m.get('fpsLike'))
        candidate = next(m for m in unconfirmed_models
                         if m['strategy'] == 'STANDARD_FPS' and m['scale'] == scale)
        assert candidate['dialogueVerified'] is False
        assert quality(candidate) not in {'HIGH', 'MEDIUM'}


def test_exact_text_anchors_ignore_segment_numbers_repeats_and_credits():
    from app.services.alignment import ExactTextAnchorProvider
    def cue(number, text):
        return Cue(str(number), number, number * 1000, number * 1000 + 1000, 1000,
                   text, normalize_text(text), 'test')
    ref = [cue(1, 'This is an independent dialogue.'), cue(2, 'Repeated dialogue with several words.'),
           cue(3, 'Repeated dialogue with several words.'), cue(4, 'Tłumaczenie: Example Subtitle Author Team')]
    pl = [cue(50, 'Zupełnie inna kwestia z dialogu.'), cue(80, ref[0].raw_text),
          cue(81, ref[1].raw_text), cue(82, ref[3].raw_text)]
    result = ExactTextAnchorProvider().provide(ref, pl, 100000, {})
    assert len(result) == 1
    assert (result[0].english_index, result[0].polish_index) == (0, 1)
