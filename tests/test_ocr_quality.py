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


def test_text_quality_is_unknown_without_dictionary_or_enough_text():
    report = quality_report(b"1\n00:00:01,000 --> 00:00:02,000\nHello there\n",
                            {"cueCount": 1, "firstMs": 1000, "lastMs": 1000}, frozenset())
    assert report["structuralQuality"] == "GOOD"
    assert report["textQuality"] == "UNKNOWN"


def test_quality_report_rejects_reversed_timestamps():
    with pytest.raises(InvalidOcrSrt):
        quality_report(b"1\n00:00:02,000 --> 00:00:01,000\nText\n", {"cueCount": 1})
