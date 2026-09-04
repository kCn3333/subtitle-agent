import re
import unicodedata


TIMING = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)


class InvalidOcrSrt(ValueError):
    pass


def _milliseconds(match: re.Match[str], prefix: str) -> int:
    return ((int(match[f"{prefix}h"]) * 60 + int(match[f"{prefix}m"])) * 60
            + int(match[f"{prefix}s"])) * 1000 + int(match[f"{prefix}ms"])


def quality_report(content: bytes, graphic_timeline: dict | None) -> dict:
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvalidOcrSrt("Wynik OCR nie jest poprawnym UTF-8") from exc
    blocks = [block for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()) if block.strip()]
    cues: list[tuple[int, int, str]] = []
    malformed = 0
    for block in blocks:
        lines = block.splitlines()
        timing_line = next((line.strip() for line in lines if "-->" in line), "")
        match = TIMING.fullmatch(timing_line)
        if not match:
            malformed += 1
            continue
        timing_index = next(index for index, line in enumerate(lines) if "-->" in line)
        cues.append((_milliseconds(match, "s"), _milliseconds(match, "e"),
                     "\n".join(lines[timing_index + 1:]).strip()))
    reversed_count = sum(end < start for start, end, _ in cues)
    monotonic = all(cues[index][0] >= cues[index - 1][0] for index in range(1, len(cues)))
    if not cues or malformed or reversed_count or not monotonic:
        raise InvalidOcrSrt(
            f"Niepoprawna struktura SRT: cues={len(cues)}, malformed={malformed}, "
            f"reversed={reversed_count}, monotonic={monotonic}"
        )

    dialogue = "\n".join(cue[2] for cue in cues)
    visible = [character for character in dialogue if not character.isspace()]
    letters = sum(character.isalpha() for character in visible)
    replacement_count = dialogue.count("\ufffd")
    control_count = sum(unicodedata.category(character) == "Cc" and character not in "\r\n\t"
                        for character in dialogue)
    isolated = sum(0 < len([character for character in cue[2]
                            if not character.isspace() and unicodedata.category(character) != "Cc"]) <= 1
                   for cue in cues)
    empty = sum(not cue[2] for cue in cues)
    expected_count = int((graphic_timeline or {}).get("cueCount") or 0)
    expected_first = (graphic_timeline or {}).get("firstMs")
    expected_last = (graphic_timeline or {}).get("lastMs")
    count_ratio = len(cues) / expected_count if expected_count else None
    first_delta = cues[0][0] - int(expected_first) if expected_first is not None else None
    last_delta = cues[-1][0] - int(expected_last) if expected_last is not None else None
    letter_ratio = letters / len(visible) if visible else 0.0
    isolated_ratio = isolated / len(cues)
    empty_ratio = empty / len(cues)

    poor = (replacement_count > 0 or control_count > 0 or empty_ratio > .10 or letter_ratio < .35
            or (count_ratio is not None and not .70 <= count_ratio <= 1.30)
            or (first_delta is not None and abs(first_delta) > 10_000)
            or (last_delta is not None and abs(last_delta) > 15_000))
    warning = (empty > 0 or isolated_ratio > .05 or letter_ratio < .55
               or (count_ratio is not None and not .90 <= count_ratio <= 1.10)
               or (first_delta is not None and abs(first_delta) > 2_000)
               or (last_delta is not None and abs(last_delta) > 5_000))
    rating = "POOR" if poor else "WARNING" if warning else "GOOD"
    messages: list[str] = []
    if replacement_count:
        messages.append(f"Znaleziono znaki zastępcze U+FFFD: {replacement_count}")
    if control_count:
        messages.append(f"Znaleziono niedozwolone znaki sterujące: {control_count}")
    if isolated:
        messages.append(f"Segmenty z izolowanym pojedynczym glifem: {isolated}")
    if letter_ratio < .55:
        messages.append(f"Niski udział liter w tekście: {letter_ratio:.1%}")
    return {
        "quality": rating,
        "validSrt": True,
        "cueCount": len(cues),
        "graphicCueCount": expected_count or None,
        "cueCountRatio": count_ratio,
        "firstMs": cues[0][0],
        "lastStartMs": cues[-1][0],
        "lastEndMs": cues[-1][1],
        "graphicFirstMs": expected_first,
        "graphicLastMs": expected_last,
        "firstTimestampDeltaMs": first_delta,
        "lastTimestampDeltaMs": last_delta,
        "emptyCueCount": empty,
        "emptyCueRatio": empty_ratio,
        "replacementCharacterCount": replacement_count,
        "controlCharacterCount": control_count,
        "isolatedSingleGlyphCueCount": isolated,
        "isolatedSingleGlyphCueRatio": isolated_ratio,
        "letterRatio": letter_ratio,
        "malformedCueCount": malformed,
        "reversedIntervalCount": reversed_count,
        "timestampsMonotonic": monotonic,
        "warnings": messages,
    }
