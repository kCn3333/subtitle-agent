import json
import re
import unicodedata
from pathlib import Path

from app.models.media import MediaIdentity, MediaKind, MediaMatch
from app.services.process_runner import run_process
from app.services.subtitle_extraction import extract_subtitle
from app.services.srt_parser import parse_srt

SUPPORTED_MEDIA = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts"}
SUPPORTED_EXTERNAL = {".srt", ".ass", ".ssa", ".vtt"}
TEXT_SUBTITLES = {"subrip", "ass", "ssa", "webvtt", "mov_text"}
GRAPHIC_SUBTITLES = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}
RELEASE_MARKER = re.compile(
    r"(?i)(?:[. _\-\[(]+)(?:19\d{2}|20\d{2}|2160p|1080p|720p|bluray|blu-ray|web[-_. ]?dl|webrip|hdtv|x26[45]|h[. ]?26[45]).*$"
)
EPISODE_SXE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})e(\d{1,3})(?:\s*[-._ ]?\s*e(\d{1,3}))?(?!\d)")
EPISODE_X = re.compile(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{1,3})(?!\d)")
YEAR = re.compile(r"(?<!\d)((?:18|19|20|21)\d{2})(?!\d)")
TRAILING_MARKERS = re.compile(
    r"(?i)\b(?:pl|pol|polish|en|eng|english|sdh|cc|forced|ai[ ._-]?sync|720p|1080p|2160p|"
    r"bluray|blu[ ._-]?ray|web[ ._-]?dl|webrip|hdtv|x26[45]|h[ ._-]?26[45])\b.*$"
)


class UserInputError(ValueError):
    pass


def validate_media_path(value: str, roots: list[Path]) -> Path:
    if not value or "\x00" in value:
        raise UserInputError("Ścieżka nie może być pusta ani zawierać znaku NUL")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise UserInputError("Ścieżka musi być absolutna")
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise UserInputError("Wskazany plik nie istnieje lub jest niedostępny") from exc
    allowed: list[Path] = []
    for root in roots:
        try:
            allowed.append(root.resolve(strict=True))
        except (FileNotFoundError, OSError):
            continue
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise UserInputError("Ścieżka po rozwiązaniu dowiązań znajduje się poza dozwolonymi katalogami")
    if not resolved.is_file():
        raise UserInputError("Ścieżka musi wskazywać istniejący zwykły plik")
    if resolved.suffix.lower() not in SUPPORTED_MEDIA:
        raise UserInputError(f"Nieobsługiwane rozszerzenie pliku: {resolved.suffix or '(brak)'}")
    return resolved


def _number(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "N/A", "") else None
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def parse_ffprobe(payload: dict, media_path: Path) -> dict:
    streams = payload.get("streams") or []
    format_data = payload.get("format") or {}
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_tracks, subtitle_tracks = [], []
    subtitle_order = 0
    for stream in streams:
        tags, disposition = stream.get("tags") or {}, stream.get("disposition") or {}
        common = {
            "streamIndex": stream.get("index"), "codec": stream.get("codec_name") or "unknown",
            "language": tags.get("language"), "title": tags.get("title"),
            "default": bool(disposition.get("default", 0)), "forced": bool(disposition.get("forced", 0)),
            "hearingImpaired": bool(disposition.get("hearing_impaired", 0)),
        }
        if stream.get("codec_type") == "audio":
            audio_tracks.append({**common, "channels": stream.get("channels"), "channelLayout": stream.get("channel_layout")})
        elif stream.get("codec_type") == "subtitle":
            codec = common["codec"]
            subtitle_tracks.append({
                **common, "subtitleOrder": subtitle_order,
                "type": "text" if codec in TEXT_SUBTITLES else "graphic" if codec in GRAPHIC_SUBTITLES else "unknown",
            })
            subtitle_order += 1
    format_tags = format_data.get("tags") or {}
    metadata_values = [str(format_tags[key]) for key in ("date", "year", "release_date", "title")
                       if format_tags.get(key)]
    parent_values = [parent.name for parent in list(media_path.parents)[:3]]
    return {
        "path": str(media_path), "name": media_path.name, "sizeBytes": media_path.stat().st_size,
        "identity": parse_media_identity(media_path.name, parent_values + metadata_values).model_dump(mode="json"),
        "container": format_data.get("format_name"), "durationSeconds": _number(format_data.get("duration")),
        "bitrate": _integer(format_data.get("bit_rate")), "width": video.get("width"), "height": video.get("height"),
        "rFrameRate": video.get("r_frame_rate"), "avgFrameRate": video.get("avg_frame_rate"),
        "videoCodec": video.get("codec_name"), "audioTracks": audio_tracks, "embeddedSubtitles": subtitle_tracks,
    }


async def probe_media(path: Path, timeout: float) -> dict:
    result = await run_process([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ], timeout)
    try:
        return parse_ffprobe(json.loads(result.stdout), path)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe zwrócił niepoprawny JSON") from exc


def _release_stem(stem: str) -> str:
    return RELEASE_MARKER.sub("", stem).rstrip(". _-") or stem


def _normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = TRAILING_MARKERS.sub("", value)
    value = re.sub(r"[\[\](){}]", " ", value)
    value = re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def parse_media_identity(filename: str, supplemental_values: list[str] | None = None) -> MediaIdentity:
    stem = Path(filename).stem
    year_match = YEAR.search(stem)
    if not year_match:
        year_match = next((match for value in supplemental_values or [] if (match := YEAR.search(value))), None)
    year = int(year_match.group(1)) if year_match else None
    episode_match = EPISODE_SXE.search(stem) or EPISODE_X.search(stem)
    if episode_match:
        season, episode = int(episode_match.group(1)), int(episode_match.group(2))
        episode_end = int(episode_match.group(3)) if len(episode_match.groups()) >= 3 and episode_match.group(3) else None
        title_part = YEAR.sub(" ", stem[:episode_match.start()])
        series_title = _normalize_title(title_part) or None
        return MediaIdentity(kind=MediaKind.EPISODE, series_title=series_title, season=season, episode=episode,
                             episode_end=episode_end, year=year, normalized_title=series_title or "")
    title_part = stem[:year_match.start()] if year_match else _release_stem(stem)
    normalized = _normalize_title(title_part)
    return MediaIdentity(kind=MediaKind.MOVIE if normalized else MediaKind.UNKNOWN,
                         year=year, normalized_title=normalized)


def match_media_identity(media: MediaIdentity, candidate: MediaIdentity) -> MediaMatch:
    if media.kind == MediaKind.EPISODE and candidate.kind == MediaKind.EPISODE:
        if media.season != candidate.season:
            return MediaMatch(accepted=False, automatic=False, confidence=0,
                              reasons=[f"sprzeczny sezon: S{candidate.season:02d} zamiast S{media.season:02d}"])
        if media.episode != candidate.episode or media.episode_end != candidate.episode_end:
            media_id = f"E{media.episode:02d}" + (f"-E{media.episode_end:02d}" if media.episode_end is not None else "")
            candidate_id = f"E{candidate.episode:02d}" + (f"-E{candidate.episode_end:02d}" if candidate.episode_end is not None else "")
            return MediaMatch(accepted=False, automatic=False, confidence=0,
                              reasons=[f"sprzeczny odcinek: {candidate_id} zamiast {media_id}"])
        reasons = [f"zgodny identyfikator S{media.season:02d}E{media.episode:02d}"]
        if media.normalized_title and candidate.normalized_title and candidate.normalized_title != media.normalized_title:
            return MediaMatch(accepted=False, automatic=False, confidence=0,
                              reasons=["niezgodny znormalizowany tytuł serialu"])
        if media.normalized_title and candidate.normalized_title == media.normalized_title:
            reasons.append("zgodny znormalizowany tytuł serialu")
        if media.year is not None and candidate.year is not None and media.year != candidate.year:
            return MediaMatch(accepted=False, automatic=False, confidence=0,
                              reasons=[f"sprzeczny rok produkcji: {candidate.year} zamiast {media.year}"])
        if media.year is not None and candidate.year is not None:
            reasons.append("zgodny rok produkcji")
            confidence = 1.0
        else:
            reasons.append("rok produkcji nie występuje po obu stronach")
            confidence = .9
        return MediaMatch(accepted=True, automatic=True, confidence=confidence, reasons=reasons)
    if media.kind == MediaKind.EPISODE or candidate.kind == MediaKind.EPISODE:
        if not media.normalized_title or media.normalized_title != candidate.normalized_title:
            return MediaMatch(accepted=False, automatic=False, confidence=0,
                              reasons=["brak zgodnego znormalizowanego tytułu przy niepełnym identyfikatorze odcinka"])
        return MediaMatch(accepted=True, automatic=False, confidence=.4,
                          reasons=["brak identyfikatora odcinka po jednej stronie; dopasowanie niejednoznaczne"])
    if not media.normalized_title or media.normalized_title != candidate.normalized_title:
        return MediaMatch(accepted=False, automatic=False, confidence=0,
                          reasons=["niezgodny znormalizowany tytuł filmu"])
    if media.year is not None and candidate.year is not None and media.year != candidate.year:
        return MediaMatch(accepted=False, automatic=False, confidence=0,
                          reasons=[f"sprzeczny rok: {candidate.year} zamiast {media.year}"])
    reasons = ["zgodny znormalizowany tytuł filmu"]
    if media.year is not None and candidate.year is not None:
        reasons.append("zgodny rok filmu")
        return MediaMatch(accepted=True, automatic=True, confidence=1, reasons=reasons)
    reasons.append("rok nie występuje po obu stronach")
    return MediaMatch(accepted=True, automatic=True, confidence=.85, reasons=reasons)


def _flags(name: str) -> dict:
    lowered = name.casefold()
    tokens = set(re.split(r"[. _\-\[\]()]+", lowered))
    return {
        "languageHint": "pl" if tokens & {"pl", "pol", "polish"} else "en" if tokens & {"en", "eng", "english"} else None,
        "forced": "forced" in tokens, "sdh": "sdh" in tokens, "cc": "cc" in tokens,
        "commentary": "commentary" in tokens or "director" in tokens, "aiSync": "ai-sync" in lowered,
    }


def discover_external_subtitles_with_rejections(
    media_path: Path, known_media_identity: MediaIdentity | None = None
) -> tuple[list[dict], list[dict], int]:
    results: list[dict] = []
    rejected: list[dict] = []
    ignored = 0
    media_directory = media_path.parent.resolve(strict=True)
    media_identity = known_media_identity or parse_media_identity(
        media_path.name, [parent.name for parent in list(media_path.parents)[:3]]
    )
    for entry in media_path.parent.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in SUPPORTED_EXTERNAL:
            continue
        try:
            resolved_entry = entry.resolve(strict=True)
        except OSError:
            continue
        if not resolved_entry.is_relative_to(media_directory):
            continue
        candidate_identity = parse_media_identity(entry.name)
        match = match_media_identity(media_identity, candidate_identity)
        if not match.accepted:
            # Explicitly different episodes/movies are not candidates for this
            # medium. Keep only an aggregate count to avoid huge season reports.
            ignored += 1
            continue
        identity_match = {"status": "MATCH" if match.accepted else "NO_MATCH", "confidence": match.confidence,
                          "automatic": match.automatic, "reasons": match.reasons}
        item = {"path": str(resolved_entry), "name": entry.name, "format": entry.suffix.lower().lstrip("."),
                "mediaIdentity": candidate_identity.model_dump(mode="json"),
                "identityMatch": identity_match, **_flags(entry.name)}
        if entry.suffix.lower() == ".srt":
            try:
                item["analysis"] = parse_srt(resolved_entry).to_dict()
            except OSError as exc:
                item["analysis"] = {"warnings": [f"Nie można odczytać pliku: {type(exc).__name__}"]}
        results.append(item)
    return (sorted(results, key=lambda item: item["name"].casefold()),
            sorted(rejected, key=lambda item: item["name"].casefold()), ignored)


def discover_external_subtitles(media_path: Path) -> list[dict]:
    accepted, _, _ = discover_external_subtitles_with_rejections(media_path)
    return accepted


def _penalty_text(item: dict) -> str:
    return " ".join(str(item.get(key) or "") for key in ("title", "name")).casefold()


def rank_english(embedded: list[dict], external: list[dict]) -> list[dict]:
    # The reference source for synchronization is deliberately limited to
    # embedded tracks; external files are evaluated as Polish candidates.
    candidates = []
    for item in embedded:
        language = (item.get("language") or "").casefold()
        label = _penalty_text(item)
        if language in {"eng", "en", "english"} or "english" in label:
            candidates.append({**item, "sourceType": "embedded"})
    ranked = []
    for item in candidates:
        score, reasons, text = 0, [], _penalty_text(item)
        language = (item.get("language") or item.get("languageHint") or "").casefold()
        if language in {"eng", "en", "english"}: score += 60; reasons.append("+60 język angielski")
        if "full dialogue" in text: score += 20; reasons.append("+20 pełne dialogi")
        if item.get("default"): score += 12; reasons.append("+12 ścieżka domyślna")
        if item.get("type") == "text" or item.get("format") in {"srt", "ass", "ssa", "vtt"}: score += 10; reasons.append("+10 napisy tekstowe")
        penalties = [("commentary", -55), ("director", -45), ("sdh", -18), (" cc", -18),
                     ("signs", -35), ("songs", -30), ("parts only", -45), ("hearing", -18)]
        for token, points in penalties:
            if token in text or item.get(token.strip().replace(" ", ""), False): score += points; reasons.append(f"{points} {token.strip()}")
        if item.get("forced"): score -= 40; reasons.append("-40 forced")
        if item.get("hearingImpaired"): score -= 18; reasons.append("-18 hearing impaired")
        ranked.append({**item, "score": score, "reasons": reasons})
    return sorted(ranked, key=lambda item: (-item["score"], str(item.get("name") or item.get("streamIndex"))))


def rank_polish(media: dict, external: list[dict], embedded: list[dict]) -> list[dict]:
    candidates = [{**item, "sourceType": "external"} for item in external] + [{**item, "sourceType": "embedded"} for item in embedded]
    ranked = []
    duration = media.get("durationSeconds")
    for item in candidates:
        score, reasons, text = 0, [], _penalty_text(item)
        language = (item.get("language") or item.get("languageHint") or item.get("analysis", {}).get("detected_language") or "").casefold()
        if language in {"pol", "pl", "polish"}: score += 60; reasons.append("+60 język polski")
        if item.get("sourceType") == "external" and item.get("format") == "srt": score += 25; reasons.append("+25 zewnętrzny SRT")
        if item.get("sourceType") == "external": score += 10; reasons.append("+10 nazwa zgodna z medium")
        if item.get("aiSync"): score -= 100; reasons.append("-100 wynik AI-Sync")
        for token, points in (("commentary", -50), ("sdh", -15), ("forced", -35)):
            if token in text or item.get(token): score += points; reasons.append(f"{points} {token}")
        analysis = item.get("analysis") or {}
        structural = analysis.get("malformed_segments", 0) + analysis.get("reversed_intervals", 0)
        if structural: score -= min(40, structural * 4); reasons.append(f"-{min(40, structural * 4)} błędy struktury")
        if 0 < analysis.get("segment_count", 999) < 50: score -= 25; reasons.append("-25 bardzo mało segmentów")
        compatibility_warnings: list[str] = []
        timing_compatibility = "UNKNOWN"
        reason_code = None
        last_time = analysis.get("last_time")
        if duration and last_time is not None:
            timing_compatibility = "COMPATIBLE"
            overrun = last_time - duration
            if overrun > 5:
                compatibility_warnings.append(
                    f"Napisy kończą się {overrun:.3f} s po zakończeniu materiału"
                )
            if overrun > max(60.0, duration * 0.03):
                timing_compatibility = "INCOMPATIBLE"
                reason_code = "LIKELY_DIFFERENT_EDITION"
                score -= 80
                reasons.append("-80 prawdopodobnie inna wersja lub produkcja")
        identity_automatic = item.get("identityMatch", {}).get("automatic", True)
        if item.get("sourceType") == "external" and identity_automatic is False:
            score -= 80; reasons.append("-80 niejednoznaczna tożsamość medium")
        eligible = not item.get("aiSync", False) and identity_automatic and timing_compatibility != "INCOMPATIBLE"
        ranked.append({**item, "score": score, "reasons": reasons,
                       "timingCompatibility": timing_compatibility,
                       "compatibilityWarnings": compatibility_warnings,
                       "reasonCode": reason_code, "eligibleByDefault": eligible})
    return sorted(ranked, key=lambda item: (-item["score"], str(item.get("name") or item.get("streamIndex"))))


async def extract_reference(reference: dict | None, media_path: Path, work_dir: Path, timeout: float) -> str | None:
    if not reference or reference.get("sourceType") != "embedded":
        return None
    stream_index = int(reference["streamIndex"])
    result = await extract_subtitle(reference, media_path, work_dir, timeout,
                                    basename=f"reference-stream-{stream_index}", keep_text_original=False)
    return str(result.files[0]) if result.files else None
