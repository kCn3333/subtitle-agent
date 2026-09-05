import re
import unicodedata
from functools import lru_cache
from pathlib import Path


TIMING = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})\s+-->\s+"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)
WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
SLASH_AS_LETTER = re.compile(
    r"(?i)(?<!\w)/(?=[a-z]{2,})|(?<=[a-z]{2})/(?!\w)|(?<=[a-z])/(?=[a-z])"
)
MISSING_APOSTROPHE = {
    "arent", "cant", "couldnt", "didnt", "doesnt", "dont", "hadnt", "hasnt", "havent",
    "hed", "hell", "hes", "id", "ill", "im", "isnt", "itll", "its", "ive", "shouldnt",
    "theyre", "theyve", "wasnt", "werent", "wont", "wouldnt", "youd", "youll", "youre", "youve",
}
DICTIONARY_PATHS = (Path("/usr/share/dict/american-english"), Path("/usr/share/dict/words"))


class InvalidOcrSrt(ValueError):
    pass


def _milliseconds(match: re.Match[str], prefix: str) -> int:
    return ((int(match[f"{prefix}h"]) * 60 + int(match[f"{prefix}m"])) * 60
            + int(match[f"{prefix}s"])) * 1000 + int(match[f"{prefix}ms"])


@lru_cache(maxsize=1)
def _english_dictionary() -> frozenset[str] | None:
    source = next((path for path in DICTIONARY_PATHS if path.is_file()), None)
    if source is None:
        return None
    words = {line.strip().casefold().replace("’", "'") for line in source.read_text(
        encoding="utf-8", errors="ignore").splitlines() if line.strip()}
    return frozenset(words) if words else None


def quality_report(content: bytes, graphic_timeline: dict | None,
                   dictionary: frozenset[str] | set[str] | None = None) -> dict:
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

    structural_poor = (empty_ratio > .10
                       or (count_ratio is not None and not .70 <= count_ratio <= 1.30)
                       or (first_delta is not None and abs(first_delta) > 10_000)
                       or (last_delta is not None and abs(last_delta) > 15_000))
    structural_warning = (empty > 0
                          or (count_ratio is not None and not .90 <= count_ratio <= 1.10)
                          or (first_delta is not None and abs(first_delta) > 2_000)
                          or (last_delta is not None and abs(last_delta) > 5_000))
    structural_quality = "POOR" if structural_poor else "WARNING" if structural_warning else "GOOD"

    effective_dictionary = dictionary if dictionary is not None else _english_dictionary()
    words = [match.group(0) for match in WORD.finditer(dialogue)]
    normalized_words = [word.casefold().replace("’", "'") for word in words]
    dictionary_words = [word for word in normalized_words if len(word) > 1 or word in {"a", "i"}]
    unknown_words = ([word for word in dictionary_words if word not in effective_dictionary]
                     if effective_dictionary is not None else [])
    out_of_dictionary_ratio = (len(unknown_words) / len(dictionary_words)
                               if effective_dictionary is not None and dictionary_words else None)
    pipe_as_i = len(re.findall(r"(?i)(?<!\w)\|(?!\w)|(?<=[a-z])\||\|(?=[a-z])", dialogue))
    slash_as_letter = len(SLASH_AS_LETTER.findall(dialogue))
    unusual_capitalization = sum(
        any(character.isupper() for character in word[1:]) and not word.isupper()
        for word in words
    )
    missing_apostrophes = sum(word in MISSING_APOSTROPHE for word in normalized_words)
    unknown_proper_names: list[str] = []
    if effective_dictionary is not None:
        for cue in cues:
            for line in cue[2].splitlines():
                line_words = WORD.findall(line)
                for word in line_words[1:]:
                    normalized = word.casefold().replace("’", "'")
                    if word[:1].isupper() and not word.isupper() and normalized not in effective_dictionary:
                        unknown_proper_names.append(word)
    suspicious_count = (replacement_count + control_count + pipe_as_i + slash_as_letter
                        + unusual_capitalization + missing_apostrophes + len(unknown_proper_names))
    text_poor = (replacement_count > 0 or control_count > 0 or letter_ratio < .35
                 or (out_of_dictionary_ratio is not None and len(dictionary_words) >= 20
                     and out_of_dictionary_ratio > .65))
    text_warning = (suspicious_count > 0 or isolated_ratio > .05 or letter_ratio < .55
                    or (out_of_dictionary_ratio is not None and len(dictionary_words) >= 20
                        and out_of_dictionary_ratio > .35))
    if text_poor:
        text_quality = "POOR"
    elif text_warning:
        text_quality = "WARNING"
    elif effective_dictionary is None or len(dictionary_words) < 20:
        text_quality = "UNKNOWN"
    else:
        text_quality = "GOOD"

    structural_messages: list[str] = []
    text_messages: list[str] = []
    if structural_quality != "GOOD":
        structural_messages.append("Timeline OCR różni się od technicznego timeline'u referencji")
    if replacement_count:
        text_messages.append(f"Znaleziono znaki zastępcze U+FFFD: {replacement_count}")
    if control_count:
        text_messages.append(f"Znaleziono niedozwolone znaki sterujące: {control_count}")
    if isolated:
        text_messages.append(f"Segmenty z izolowanym pojedynczym glifem: {isolated}")
    if letter_ratio < .55:
        text_messages.append(f"Niski udział liter w tekście: {letter_ratio:.1%}")
    if pipe_as_i:
        text_messages.append(f"Podejrzany znak | zamiast I/l: {pipe_as_i}")
    if slash_as_letter:
        text_messages.append(f"Podejrzany ukośnik zamiast litery: {slash_as_letter}")
    if unusual_capitalization:
        text_messages.append(f"Nietypowa kapitalizacja słów: {unusual_capitalization}")
    if missing_apostrophes:
        text_messages.append(f"Prawdopodobnie brakujące apostrofy: {missing_apostrophes}")
    if unknown_proper_names:
        text_messages.append(f"Nierozpoznane potencjalne nazwy własne: {len(unknown_proper_names)}")
    if out_of_dictionary_ratio is not None and len(dictionary_words) >= 20 and out_of_dictionary_ratio > .35:
        text_messages.append(f"Wysoki udział słów spoza słownika: {out_of_dictionary_ratio:.1%}")
    return {
        "structuralQuality": structural_quality,
        "textQuality": text_quality,
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
        "wordCount": len(dictionary_words),
        "dictionaryAvailable": effective_dictionary is not None,
        "outOfDictionaryWordCount": len(unknown_words) if effective_dictionary is not None else None,
        "outOfDictionaryWordRatio": out_of_dictionary_ratio,
        "pipeAsLetterCount": pipe_as_i,
        "slashAsLetterCount": slash_as_letter,
        "unusualCapitalizationCount": unusual_capitalization,
        "missingApostropheCount": missing_apostrophes,
        "unrecognizedProperNameCount": len(unknown_proper_names) if effective_dictionary is not None else None,
        "unrecognizedProperNamesSample": sorted(set(unknown_proper_names), key=str.casefold)[:20],
        "malformedCueCount": malformed,
        "reversedIntervalCount": reversed_count,
        "timestampsMonotonic": monotonic,
        "structuralWarnings": structural_messages,
        "textWarnings": text_messages,
        "warnings": structural_messages + text_messages,
    }
