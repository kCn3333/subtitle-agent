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
    report = quality_report(content, {"cueCount": 760, "firstMs": 46_463, "lastMs": 3_017_746})
    assert report["quality"] in {"GOOD", "WARNING"}
    assert report["cueCountRatio"] == pytest.approx(750 / 760)
    assert report["timestampsMonotonic"] is True
    assert report["replacementCharacterCount"] == 0


def test_quality_report_records_suspicious_text_metrics():
    report = quality_report(
        b"1\n00:00:01,000 --> 00:00:02,000\nBad\xef\xbf\xbd\n\n"
        b"2\n00:00:03,000 --> 00:00:04,000\nX\n",
        {"cueCount": 2, "firstMs": 1000, "lastMs": 3000},
    )
    assert report["quality"] == "POOR"
    assert report["replacementCharacterCount"] == 1
    assert report["isolatedSingleGlyphCueCount"] == 1


def test_quality_report_rejects_reversed_timestamps():
    with pytest.raises(InvalidOcrSrt):
        quality_report(b"1\n00:00:02,000 --> 00:00:01,000\nText\n", {"cueCount": 1})
