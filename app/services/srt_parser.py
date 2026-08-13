import re
from dataclasses import asdict, dataclass
from pathlib import Path

from charset_normalizer import from_bytes

TIME_RE = re.compile(
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sms>\d{3})\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2}):(?P<es>\d{2})[,.](?P<ems>\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def _seconds(match: re.Match[str], prefix: str) -> float:
    return (int(match[f"{prefix}h"]) * 3600 + int(match[f"{prefix}m"]) * 60
            + int(match[f"{prefix}s"]) + int(match[f"{prefix}ms"]) / 1000)


@dataclass
class SrtAnalysis:
    encoding: str
    encoding_confidence: float | None
    segment_count: int
    first_time: float | None
    last_time: float | None
    coverage_seconds: float
    malformed_segments: int
    reversed_intervals: int
    overlapping_segments: int
    monotonic: bool
    detected_language: str | None
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _language(text: str) -> str | None:
    lowered = f" {TAG_RE.sub(' ', text).lower()} "
    polish = sum(lowered.count(token) for token in (" się ", " nie ", " jest ", " że ", "ę", "ą", "ł", "ż", "ź", "ć", "ń"))
    english = sum(lowered.count(token) for token in (" the ", " and ", " you ", " is ", " are ", " not ", " to "))
    if max(polish, english) < 3 or abs(polish - english) < 2:
        return None
    return "pl" if polish > english else "en"


def parse_srt(path: Path) -> SrtAnalysis:
    raw = path.read_bytes()
    best = from_bytes(raw).best()
    encoding = "utf_8_sig" if raw.startswith(b"\xef\xbb\xbf") else (best.encoding if best else "utf_8")
    confidence = None
    if best is not None:
        confidence = max(0.0, min(1.0, 1.0 - (best.chaos / 100 if best.chaos > 1 else best.chaos)))
    warnings: list[str] = []
    if confidence is not None and confidence < 0.6:
        warnings.append("Niska pewność wykrycia kodowania")
    try:
        text = raw.decode(encoding or "utf_8", errors="strict")
    except (LookupError, UnicodeDecodeError):
        for fallback in ("utf-8-sig", "cp1250", "iso-8859-2", "cp1252"):
            try:
                text = raw.decode(fallback)
                encoding = fallback
                warnings.append("Użyto kodowania awaryjnego")
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
            encoding = "utf-8-replace"
            warnings.append("Plik zawiera niedekodowalne bajty")

    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()) if text.strip() else []
    intervals: list[tuple[float, float]] = []
    malformed = 0
    dialogue: list[str] = []
    for block in blocks:
        match = TIME_RE.search(block)
        if not match:
            malformed += 1
            continue
        start, end = _seconds(match, "s"), _seconds(match, "e")
        intervals.append((start, end))
        dialogue.append(block[match.end():])
    reversed_count = sum(end < start for start, end in intervals)
    overlaps = sum(intervals[i][0] < intervals[i - 1][1] for i in range(1, len(intervals)))
    monotonic = all(intervals[i][0] >= intervals[i - 1][0] for i in range(1, len(intervals)))
    if malformed:
        warnings.append(f"Uszkodzone lub puste segmenty: {malformed}")
    if reversed_count:
        warnings.append(f"Odwrócone przedziały czasu: {reversed_count}")
    if overlaps:
        warnings.append(f"Nakładające się segmenty: {overlaps}")
    if not monotonic:
        warnings.append("Znaczniki czasu nie są monotoniczne")
    valid = [(start, end) for start, end in intervals if end >= start]
    return SrtAnalysis(
        encoding=encoding or "unknown", encoding_confidence=confidence,
        segment_count=len(intervals), first_time=intervals[0][0] if intervals else None,
        last_time=intervals[-1][1] if intervals else None,
        coverage_seconds=sum(end - start for start, end in valid), malformed_segments=malformed,
        reversed_intervals=reversed_count, overlapping_segments=overlaps, monotonic=monotonic,
        detected_language=_language("\n".join(dialogue)), warnings=warnings,
    )
