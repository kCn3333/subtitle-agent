import json
import re
from pathlib import Path

from app.services.process_runner import run_process
from app.services.srt_parser import parse_srt

SUPPORTED_MEDIA = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts"}
SUPPORTED_EXTERNAL = {".srt", ".ass", ".ssa", ".vtt"}
TEXT_SUBTITLES = {"subrip", "ass", "ssa", "webvtt", "mov_text"}
GRAPHIC_SUBTITLES = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}
RELEASE_MARKER = re.compile(
    r"(?i)(?:[. _\-\[(]+)(?:19\d{2}|20\d{2}|2160p|1080p|720p|bluray|blu-ray|web[-_. ]?dl|webrip|hdtv|x26[45]|h[. ]?26[45]).*$"
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
    return {
        "path": str(media_path), "name": media_path.name, "sizeBytes": media_path.stat().st_size,
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


def _name_matches(media_stem: str, subtitle_stem: str) -> bool:
    media_full = media_stem.casefold()
    media_clean = _release_stem(media_stem).casefold()
    candidate = subtitle_stem.casefold()
    return candidate.startswith(media_full) or candidate.startswith(media_clean)


def _flags(name: str) -> dict:
    lowered = name.casefold()
    tokens = set(re.split(r"[. _\-\[\]()]+", lowered))
    return {
        "languageHint": "pl" if tokens & {"pl", "pol", "polish"} else "en" if tokens & {"en", "eng", "english"} else None,
        "forced": "forced" in tokens, "sdh": "sdh" in tokens, "cc": "cc" in tokens,
        "commentary": "commentary" in tokens or "director" in tokens, "aiSync": "ai-sync" in lowered,
    }


def discover_external_subtitles(media_path: Path) -> list[dict]:
    results: list[dict] = []
    media_directory = media_path.parent.resolve(strict=True)
    for entry in media_path.parent.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in SUPPORTED_EXTERNAL:
            continue
        try:
            resolved_entry = entry.resolve(strict=True)
        except OSError:
            continue
        if not resolved_entry.is_relative_to(media_directory):
            continue
        if not _name_matches(media_path.stem, entry.stem):
            continue
        item = {"path": str(resolved_entry), "name": entry.name, "format": entry.suffix.lower().lstrip("."), **_flags(entry.name)}
        if entry.suffix.lower() == ".srt":
            try:
                item["analysis"] = parse_srt(resolved_entry).to_dict()
            except OSError as exc:
                item["analysis"] = {"warnings": [f"Nie można odczytać pliku: {type(exc).__name__}"]}
        results.append(item)
    return sorted(results, key=lambda item: item["name"].casefold())


def _penalty_text(item: dict) -> str:
    return " ".join(str(item.get(key) or "") for key in ("title", "name")).casefold()


def rank_english(embedded: list[dict], external: list[dict]) -> list[dict]:
    # The reference source for synchronization is deliberately limited to
    # embedded tracks; external files are evaluated as Polish candidates.
    candidates = [{**item, "sourceType": "embedded"} for item in embedded]
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
        if duration and analysis.get("last_time") and analysis["last_time"] > duration * 1.15: score -= 25; reasons.append("-25 napisy znacznie dłuższe od filmu")
        ranked.append({**item, "score": score, "reasons": reasons, "eligibleByDefault": not item.get("aiSync", False)})
    return sorted(ranked, key=lambda item: (-item["score"], str(item.get("name") or item.get("streamIndex"))))


async def extract_reference(reference: dict | None, media_path: Path, work_dir: Path, timeout: float) -> str | None:
    if not reference or reference.get("sourceType") != "embedded":
        return None
    stream_index = int(reference["streamIndex"])
    subtitle_type, codec = reference.get("type"), reference.get("codec")
    if subtitle_type == "text":
        output = work_dir / f"reference-stream-{stream_index}.srt"
        args = ["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{stream_index}", "-c:s", "srt", "-y", str(output)]
    elif codec == "hdmv_pgs_subtitle":
        output = work_dir / f"reference-stream-{stream_index}.sup"
        args = ["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{stream_index}", "-c", "copy", "-y", str(output)]
    elif codec in {"dvd_subtitle", "dvb_subtitle"}:
        output = work_dir / f"reference-stream-{stream_index}.mkv"
        args = ["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{stream_index}", "-c", "copy", "-y", str(output)]
    else:
        return None
    await run_process(args, timeout)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Wyodrębniony plik referencyjny jest pusty")
    return str(output)
