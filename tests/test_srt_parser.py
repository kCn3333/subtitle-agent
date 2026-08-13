from app.services.srt_parser import parse_srt


SRT = """2
00:00:01,000 --> 00:00:03,000
<i>To jest test.</i>

1
00:00:02,500 --> 00:00:04,000
Druga linia.
"""


def test_utf8_bom_and_overlap(tmp_path):
    path = tmp_path / "test.srt"; path.write_bytes(b"\xef\xbb\xbf" + SRT.encode())
    result = parse_srt(path)
    assert result.encoding == "utf_8_sig"
    assert result.segment_count == 2
    assert result.overlapping_segments == 1


def test_windows_1250_without_reliable_numbering(tmp_path):
    path = tmp_path / "pl.srt"
    path.write_bytes("00:00:01,000 --> 00:00:02,000\nZażółć gęślą jaźń, to jest test.\n".encode("cp1250"))
    result = parse_srt(path)
    assert result.segment_count == 1
    assert result.encoding


def test_malformed_srt_is_reported(tmp_path):
    path = tmp_path / "broken.srt"; path.write_text("broken block\n\n00:00:03,000 --> 00:00:01,000\nBad")
    result = parse_srt(path)
    assert result.malformed_segments == 1
    assert result.reversed_intervals == 1
