import pytest

from app.services.ocr_quality import InvalidOcrSrt, quality_report


def _srt(count: int, first_ms: int = 46_463, spacing_ms: int = 3_967) -> bytes:
    def stamp(value: int) -> str:
        return (f"{value // 3_600_000:02d}:{value // 60_000 % 60:02d}:"
                f"{value // 1_000 % 60:02d},{value % 1_000:03d}")

    return "\n".join(
        f"{number}\n{stamp(first_ms + (number - 1) * spacing_ms)} --> "
        f"{stamp(first_ms + (number - 1) * spacing_ms + 1500)}\nEnglish subtitle sentence {number}.\n"
        for number in range(1, count + 1)
    ).encode()


def test_quality_report_accepts_small_count_and_timing_differences():
    content = _srt(750)
    dictionary = {"english", "subtitle", "sentence"}
    report = quality_report(content, {"cueCount": 760, "firstMs": 46_463, "lastMs": 3_017_746}, dictionary)
    assert report["structuralQuality"] == "GOOD"
    assert report["textQuality"] == "GOOD"
    assert report["cueCountRatio"] == pytest.approx(750 / 760)
    assert report["timestampsMonotonic"] is True
    assert report["replacementCharacterCount"] == 0


def test_quality_report_records_suspicious_text_metrics():
    report = quality_report(
        b"1\n00:00:01,000 --> 00:00:02,000\nBad\xef\xbf\xbd\n\n"
        b"2\n00:00:03,000 --> 00:00:04,000\nX\n",
        {"cueCount": 2, "firstMs": 1000, "lastMs": 3000}, {"bad", "x"},
    )
    assert report["structuralQuality"] == "GOOD"
    assert report["textQuality"] == "POOR"
    assert report["replacementCharacterCount"] == 1
    assert report["isolatedSingleGlyphCueCount"] == 1


def test_text_quality_detects_common_ocr_language_artifacts():
    content = (
        b"1\n00:00:01,000 --> 00:00:02,000\nHello Zorblax | dont miXed wo/rd\n\n"
        b"2\n00:00:03,000 --> 00:00:04,000\nThis is readable English dialogue text\n"
    )
    dictionary = {"hello", "this", "is", "readable", "english", "dialogue", "text", "word", "mixed"}
    report = quality_report(content, {"cueCount": 2, "firstMs": 1000, "lastMs": 3000}, dictionary)
    assert report["structuralQuality"] == "GOOD"
    assert report["textQuality"] == "WARNING"
    assert report["pipeAsLetterCount"] == 1
    assert report["slashAsLetterCount"] == 1
    assert report["unusualCapitalizationCount"] == 1
    assert report["missingApostropheCount"] == 1
    assert report["unrecognizedProperNameCount"] == 1
    assert report["outOfDictionaryWordRatio"] > 0


def test_text_quality_detects_slash_at_start_of_word():
    content = b"1\n00:00:01,000 --> 00:00:02,000\nHe comes to see Johan in the /ab.\n"
    report = quality_report(content, {"cueCount": 1, "firstMs": 1000, "lastMs": 1000},
                            {"he", "comes", "to", "see", "johan", "in", "the", "lab"})
    assert report["slashAsLetterCount"] == 1
    assert "internalWordSlashCount" not in report
    assert "Podejrzany ukośnik zamiast litery: 1" in report["textWarnings"]


def test_text_quality_detects_slash_runs_but_ignores_word_separator():
    content = (
        b"1\n00:00:01,000 --> 00:00:02,000\n//l give you the credits.\n\n"
        b"2\n00:00:03,000 --> 00:00:04,000\n/l or l/ and and/or.\n"
    )
    dictionary = {"i", "give", "you", "the", "credits", "or", "and"}
    report = quality_report(content, {"cueCount": 2, "firstMs": 1000, "lastMs": 3000}, dictionary)
    assert report["slashAsLetterCount"] == 3
    assert "Podejrzany ukośnik zamiast litery: 3" in report["textWarnings"]


def test_text_quality_is_unknown_without_dictionary_or_enough_text():
    report = quality_report(b"1\n00:00:01,000 --> 00:00:02,000\nHello there\n",
                            {"cueCount": 1, "firstMs": 1000, "lastMs": 1000}, frozenset())
    assert report["structuralQuality"] == "GOOD"
    assert report["textQuality"] == "UNKNOWN"


def test_quality_report_rejects_reversed_timestamps():
    with pytest.raises(InvalidOcrSrt):
        quality_report(b"1\n00:00:02,000 --> 00:00:01,000\nText\n", {"cueCount": 1})


def test_pgs_show_hide_events_match_ocr_cues_and_final_end(pgs_ocr_case):
    report = quality_report(pgs_ocr_case['content'], pgs_ocr_case['timeline'])
    assert report['structuralQuality'] == 'GOOD'
    assert report['validSrt'] is True and report['timestampsMonotonic'] is True
    assert report['malformedCueCount'] == report['reversedIntervalCount'] == report['emptyCueCount'] == 0
    assert report['graphicCueCount'] == 4536 and report['cueCount'] == 2268
    assert report['cueCountRatio'] == .5 and report['assessedCueCountRatio'] == 1
    assert report['graphicCountInterpretation'] == 'PGS_SHOW_HIDE_EVENTS'
    assert report['graphicFirstMs'] == report['ocrFirstMs'] == 107483
    assert report['graphicLastMs'] == report['ocrLastEndMs'] == 7442728
    assert report['firstTimestampDeltaMs'] == report['lastTimestampDeltaMs'] == 0


@pytest.mark.parametrize('override', [
    {'firstMs': 507483}, {'lastMs': 7542728}, {'cueCount': 20000}, {'codec': 'dvd_subtitle'},
])
def test_pgs_pair_interpretation_does_not_hide_other_structural_problems(pgs_ocr_case, override):
    report = quality_report(pgs_ocr_case['content'], {**pgs_ocr_case['timeline'], **override})
    assert report['structuralQuality'] == 'POOR'


def test_pgs_single_event_per_cue_and_long_final_cue():
    content = b'1\n00:00:01,000 --> 00:00:30,000\nHello there\n'
    report = quality_report(content, {'codec':'hdmv_pgs_subtitle', 'cueCount':1, 'firstMs':1000, 'lastMs':30000})
    assert report['structuralQuality'] == 'GOOD'
    assert report['lastTimestampDeltaMs'] == 0
    assert report['graphicCountInterpretation'] == 'ONE_EVENT_PER_CUE'


def test_conservative_pipe_normalization_only_changes_known_dialogue_tokens():
    from app.services.ocr_quality import normalize_ocr_text

    content = ("1\r\n00:00:01,000 --> 00:00:02,000\r\n"
               "| know. -| agree. |'ve |'m |'d |'ll |’ve\r\n"
               "wo|rd |word word| |'unknown || 1|2 /l and/or\r\n").encode()
    normalized, changes = normalize_ocr_text(content)
    assert normalized == content.replace(b'| know', b'I know').replace(b'-| agree', b'-I agree').replace(
        b"|'ve", b"I've").replace(b"|'m", b"I'm").replace(b"|'d", b"I'd").replace(
        b"|'ll", b"I'll").replace('|’ve'.encode(), 'I’ve'.encode())
    assert changes['pipeToICount'] == 7
    report = quality_report(normalized, None)
    assert report['unresolvedPipeCount'] == 7
    assert report['textQuality'] != 'GOOD'
    assert any('pozostawione do weryfikacji' in message for message in report['textWarnings'])
    assert normalize_ocr_text(normalized)[0] == normalized
