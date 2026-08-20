import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from app.models.job import WorkpackTaskType
from app.services.alignment import StructuralAnchorProvider, fit_models, parse_cues, public_model, quality, select_model
from app.services.process_runner import run_process

SCHEMA_VERSION = "subtitle-workpack-v2"
SAFE_NAME = re.compile(r"[^\w .()\[\]-]+", re.UNICODE)
TEXT_CODECS = {"subrip": "srt", "ass": "ass", "ssa": "ssa", "webvtt": "vtt", "mov_text": "txt"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(value: str, fallback: str = "media") -> str:
    cleaned = SAFE_NAME.sub("_", Path(value).name).strip(" .")
    return cleaned[:180] or fallback


def safe_archive_name(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Unsafe archive entry")
    return "/".join(safe_filename(part, "file") for part in path.parts)


def timeline(path: Path, source: str) -> dict:
    cues = parse_cues(path, source)
    entries = [{
        "cue_id": cue.cue_id, "sequence": cue.sequence, "start_ms": cue.start_ms,
        "end_ms": cue.end_ms, "duration_ms": cue.duration_ms,
        "character_count": len(cue.raw_text),
        "normalized_text_sha256": hashlib.sha256(cue.normalized_text.encode("utf-8")).hexdigest(),
    } for cue in cues]
    return {"cue_count": len(entries), "first_ms": entries[0]["start_ms"] if entries else None,
            "last_ms": entries[-1]["end_ms"] if entries else None, "cues": entries, "warnings": []}


def media_summary(media: dict) -> dict:
    rate = media.get("avgFrameRate") or media.get("rFrameRate")
    decimal = None
    if rate and "/" in rate:
        numerator, denominator = rate.split("/", 1)
        try:
            decimal = round(float(numerator) / float(denominator), 6)
        except (ValueError, ZeroDivisionError):
            pass
    return {
        "name": safe_filename(media.get("name") or "media"),
        "extension": Path(media.get("name") or "").suffix.lower(),
        "container": media.get("container"),
        "duration_ms": round((media.get("durationSeconds") or 0) * 1000),
        "fps": {"fraction": rate, "decimal": decimal},
        "width": media.get("width"), "height": media.get("height"),
        "video_codec": media.get("videoCodec"),
        "identity": media.get("identity"),
        "audio_tracks": [{key: item.get(key) for key in ("streamIndex", "codec", "language", "title", "channels", "channelLayout")}
                         for item in media.get("audioTracks", [])],
    }


def subtitle_streams(media: dict) -> list[dict]:
    keys = ("streamIndex", "subtitleOrder", "codec", "language", "title", "default", "forced", "hearingImpaired", "type")
    return [{key: stream.get(key) for key in keys} for stream in media.get("embeddedSubtitles", [])]


def inspection_report(media: dict, english_ranking: list[dict], polish_ranking: list[dict],
                      rejected: list[dict], hypotheses: list[dict]) -> dict:
    duration = media.get("durationSeconds") or 0
    polish = []
    for item in polish_ranking:
        analysis = item.get("analysis") or {}
        language = (item.get("languageHint") or analysis.get("detected_language") or "").casefold()
        if item.get("sourceType") != "external" or language not in {"pl", "pol", "polish"}:
            continue
        polish.append({
            "name": item.get("name"), "score": item.get("score"), "rankingReasons": item.get("reasons", []),
            "matchConfidence": item.get("matchConfidence"), "matchReasons": item.get("matchReasons", []),
            "matchAutomatic": item.get("matchAutomatic"), "segments": analysis.get("segment_count"),
            "firstTimestamp": analysis.get("first_time"), "lastTimestamp": analysis.get("last_time"),
            "movieCoverage": ((analysis.get("last_time") or 0) / duration if duration else None),
            "structuralErrors": {
                "malformedSegments": analysis.get("malformed_segments", 0),
                "reversedIntervals": analysis.get("reversed_intervals", 0),
                "overlappingSegments": analysis.get("overlapping_segments", 0),
                "monotonic": analysis.get("monotonic"), "warnings": analysis.get("warnings", []),
            },
        })
    english_keys = ("streamIndex", "codec", "language", "title", "type", "score", "reasons",
                    "default", "forced", "hearingImpaired")
    rejected_polish = [item for item in rejected if item.get("languageHint") in {"pl", "pol", "polish"}]
    return {
        "reportVersion": 2, "mediaIdentity": media.get("identity"), "media": media_summary(media),
        "embeddedSubtitleTracks": subtitle_streams(media),
        "englishRanking": [{key: item.get(key) for key in english_keys} for item in english_ranking],
        "polishCandidates": polish, "rejectedPolishCandidates": rejected_polish,
        "synchronizationHypotheses": hypotheses,
    }


async def extract_embedded(reference: dict, media_path: Path, target: Path, timeout: float) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    index, codec = int(reference["streamIndex"]), reference.get("codec")
    outputs: list[Path] = []
    if reference.get("type") == "text":
        extension = TEXT_CODECS.get(codec, "txt")
        original = target / f"selected.original.{extension}"
        await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}", "-c:s", "copy", "-y", str(original)], timeout)
        converted = target / "selected.eng.srt"
        await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}", "-c:s", "srt", "-y", str(converted)], timeout)
        outputs = [original, converted] if original != converted else [converted]
    elif codec == "hdmv_pgs_subtitle":
        output = target / "selected.eng.sup"
        await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}", "-c", "copy", "-y", str(output)], timeout)
        outputs = [output]
    elif codec == "dvd_subtitle":
        base = target / "selected.eng.idx"
        await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}", "-c", "copy", "-f", "vobsub", "-y", str(base)], timeout)
        outputs = [target / "selected.eng.idx", target / "selected.eng.sub"]
    else:
        output = target / f"selected.eng.{safe_filename(codec or 'graphic')}"
        await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}", "-c", "copy", "-y", str(output)], timeout)
        outputs = [output]
    if any(not item.is_file() or item.stat().st_size == 0 for item in outputs):
        raise RuntimeError("Nie utworzono kompletnej referencji napisów")
    return outputs


async def graphic_timeline(media_path: Path, stream_index: int, timeout: float) -> dict:
    result = await run_process(["ffprobe", "-v", "error", "-select_streams", str(stream_index),
                                "-show_packets", "-show_entries", "packet=pts_time,duration_time,stream_index",
                                "-of", "json", str(media_path)], timeout)
    payload = json.loads(result.stdout)
    events = []
    for sequence, packet in enumerate(payload.get("packets", []), 1):
        start = round(float(packet.get("pts_time", 0)) * 1000)
        duration = round(float(packet.get("duration_time", 0)) * 1000) if packet.get("duration_time") else None
        events.append({"sequence": sequence, "start_ms": start, "end_ms": start + duration if duration is not None else None,
                       "duration_ms": duration, "stream_index": packet.get("stream_index", stream_index)})
    return {"event_count": len(events), "events": events}


def copy_polish_candidates(ranking: list[dict], target: Path, maximum: int) -> tuple[list[dict], list[dict]]:
    target.mkdir(parents=True, exist_ok=True)
    candidates = [item for item in ranking if item.get("sourceType") == "external" and item.get("eligibleByDefault", True) and
                  (item.get("languageHint") == "pl" or
                   item.get("analysis", {}).get("detected_language") == "pl")]
    selected = candidates[:maximum]
    included = []
    for number, item in enumerate(selected, 1):
        source = Path(item["path"])
        if source.is_symlink() or not source.is_file():
            continue
        extension = source.suffix.lower() if source.suffix.lower() in {".srt", ".ass", ".ssa", ".vtt"} else ".sub"
        archive_name = f"polish/candidate-{number:03d}.pl{extension}"
        destination = target.parent / archive_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        included.append({**item, "archiveName": archive_name, "originalName": source.name,
                         "sizeBytes": destination.stat().st_size, "sha256": sha256_file(destination),
                         "generatedResult": bool(item.get("aiSync"))})
    omitted = [dict(item, omissionReason="WORKPACK_MAX_POLISH_CANDIDATES") for item in candidates[maximum:]]
    return included, omitted


def diagnostic_hypotheses(reference_path: Path | None, polish: list[dict], job_dir: Path, duration_ms: int) -> list[dict]:
    if not reference_path or not reference_path.suffix.lower() == ".srt":
        return []
    english = parse_cues(reference_path, "reference")
    results = []
    for item in polish:
        candidate = job_dir / item["archiveName"] if item.get("archiveName") else Path(item["path"])
        if candidate.suffix.lower() != ".srt":
            continue
        candidate_name = item.get("archiveName") or item.get("name") or candidate.name
        cues = parse_cues(candidate, candidate_name)
        anchors = StructuralAnchorProvider().provide(english, cues, duration_ms, {})
        models = fit_models(anchors)
        selected = select_model(models)
        grade = quality(selected)
        model_names = {"IDENTITY": "zgodne osie czasu", "GLOBAL_OFFSET": "stałe przesunięcie",
                       "AFFINE_DRIFT": "dryf liniowy", "PIECEWISE_LINEAR": "model odcinkowy"}
        results.append({
            "candidate": candidate_name, "originalName": item.get("originalName") or item.get("name"),
            "matchConfidence": item.get("matchConfidence"), "matchReasons": item.get("matchReasons", []),
            "englishSegments": len(english), "polishSegments": len(cues), "anchorCount": len(anchors),
            "hypothesis": model_names.get((selected or {}).get("strategy"), "brak wiarygodnej hipotezy"),
            "offsetMs": (selected or {}).get("offsetMs"), "spreadMs": (selected or {}).get("p95ResidualMs"),
            "analysisCoverage": (selected or {}).get("coverage", 0), "confidence": grade,
            "sufficientAnchors": grade in {"HIGH", "MEDIUM"},
            "verification": "Porównaj segmenty z początku, środka i końca",
            "models": [{**public_model(model), "quality": quality(model),
                        "rejection_reasons": [] if quality(model) in {"HIGH", "MEDIUM"} else
                        ["Za mało wiarygodnych kotwic do uznania hipotezy za synchronizację"]}
                       for model in models],
        })
    return results


REQUESTS = {
    WorkpackTaskType.INSPECT: "Oceń wykryte napisy: wskaż pełne dialogi, komentarz, SDH, forced i ścieżki częściowe. Uzasadnij wybór najlepszego źródła.",
    WorkpackTaskType.PREPARE_SYNC: "Dopasuj polskie napisy znajdujące się w katalogu polish/ do angielskiej referencji z reference/selected/. Angielskie napisy są poprawnie zsynchronizowane z filmem. Zachowaj polską treść.",
    WorkpackTaskType.PREPARE_TRANSLATION: "Wykonaj kompletne profesjonalne tłumaczenie angielskich napisów na język polski, zachowując ich synchronizację.",
    WorkpackTaskType.SYNC_ONLY: "Dopasuj polskie napisy znajdujące się w katalogu polish/ do angielskiej referencji z reference/selected/. Angielskie napisy są poprawnie zsynchronizowane z filmem. Zachowaj polską treść, o ile nie zawiera oczywistych błędów technicznych.",
    WorkpackTaskType.LANGUAGE_REVIEW: "Popraw polskie napisy pod względem gramatycznym, stylistycznym, ortograficznym i interpunkcyjnym. Zachowaj dokładnie synchronizację wskazanego polskiego pliku bazowego.",
    WorkpackTaskType.SYNC_AND_LANGUAGE_REVIEW: "Dopasuj polskie napisy do poprawnie zsynchronizowanej angielskiej referencji, a następnie wykonaj profesjonalną korektę językową. Zachowaj znaczenie dialogów, naturalny język polski i czytelność napisów.",
    WorkpackTaskType.TRANSLATE_TO_POLISH: "Wykonaj kompletne profesjonalne tłumaczenie angielskich napisów na język polski, zachowując ich synchronizację.",
    WorkpackTaskType.INSPECT_SUBTITLES: "Oceń wykryte napisy: wskaż pełne dialogi, komentarz, SDH, forced i ścieżki częściowe. Uzasadnij wybór najlepszego źródła.",
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def request_text(task: WorkpackTaskType, manifest: dict) -> str:
    polish = "\n".join(f"- {item['archiveName']} ({item['originalName']})" for item in manifest["polish_candidates"]) or "- brak"
    warnings = "\n".join(f"- {item}" for item in manifest["warnings"]) or "- brak"
    if task in {WorkpackTaskType.INSPECT, WorkpackTaskType.INSPECT_SUBTITLES}:
        return (f"# Zadanie: {task.value}\n\n{REQUESTS[task]}\n\nNie generuj ani nie synchronizuj napisów. "
                "Opisz ustalenia na podstawie plików analysis/ i manifest.json.\n\n"
                f"## Ostrzeżenia\n{warnings}\n")
    return (f"# Zadanie: {task.value}\n\n{REQUESTS[task]}\n\nZwróć kompletny, poprawny plik UTF-8 SRT o nazwie "
            f"`{manifest['expected_output']['filename']}`. Zachowaj prawidłową numerację i timing. "
            "Manifest `manifest.json` jest źródłem danych technicznych.\n\n## Polskie materiały\n"
            f"{polish}\n\n## Ostrzeżenia\n{warnings}\n")


def build_zip(job_dir: Path, media_stem: str, maximum_bytes: int, maximum_files: int,
              include_paths: set[str] | None = None) -> tuple[Path, int, str, list[str]]:
    version = 1
    while (job_dir / f"{safe_filename(media_stem)}.subtitle-workpack-v{version:03d}.zip").exists():
        version += 1
    archive = job_dir / f"{safe_filename(media_stem)}.subtitle-workpack-v{version:03d}.zip"
    excluded = {archive.name, "checksums.sha256"}
    files = sorted((path for path in job_dir.rglob("*") if path.is_file() and not path.is_symlink()
                    and path.name not in excluded and path.suffix.lower() != ".zip"
                    and (include_paths is None or path.relative_to(job_dir).as_posix() in include_paths)),
                   key=lambda path: path.relative_to(job_dir).as_posix())
    omitted: list[str] = []
    priority = lambda path: (0 if path.name == "manifest.json" else
                             1 if "reference/selected/" in path.relative_to(job_dir).as_posix() else
                             2 if path.relative_to(job_dir).as_posix().startswith("polish/") else
                             3, path.relative_to(job_dir).as_posix())
    if len(files) + 1 > maximum_files:
        ordered = sorted(files, key=priority)
        keep, overflow = ordered[:maximum_files - 1], ordered[maximum_files - 1:]
        files, omitted = sorted(keep, key=lambda path: path.relative_to(job_dir).as_posix()), [
            item.relative_to(job_dir).as_posix() for item in overflow]
    # Use an uncompressed-size budget, which is conservative for deflated ZIPs.
    # Preserve the manifest and selected reference first; omissions are explicit.
    selected_files, used = [], 0
    payload_budget = max(0, maximum_bytes - 4096 - len(files) * 512)
    for path in sorted(files, key=priority):
        size = path.stat().st_size
        if used + size <= payload_budget or priority(path)[0] <= 1:
            selected_files.append(path); used += size
        else:
            omitted.append(path.relative_to(job_dir).as_posix())
    files = sorted(selected_files, key=lambda path: path.relative_to(job_dir).as_posix())
    if omitted and (job_dir / "manifest.json") in files:
        manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["omitted_files"] = sorted(set(omitted))
        manifest.setdefault("warnings", []).append(f"Pominięto {len(set(omitted))} plików z powodu limitów workpacka")
        write_json(job_dir / "manifest.json", manifest)
    checksums = "".join(f"{sha256_file(path)}  {safe_archive_name(path.relative_to(job_dir).as_posix())}\n" for path in files)
    checksum_path = job_dir / "checksums.sha256"
    checksum_path.write_text(checksums, encoding="utf-8")
    files.append(checksum_path)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(files, key=lambda item: item.relative_to(job_dir).as_posix()):
            name = safe_archive_name(path.relative_to(job_dir).as_posix())
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, path.read_bytes())
            if archive.stat().st_size > maximum_bytes and not any(
                    "reference/selected/" in item.relative_to(job_dir).as_posix() and item.stat().st_size > maximum_bytes
                    for item in files):
                raise ValueError("WORKPACK_MAX_ARCHIVE_BYTES exceeded")
    return archive, version, sha256_file(archive), omitted
