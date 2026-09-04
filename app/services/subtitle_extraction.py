import re
from dataclasses import dataclass
from pathlib import Path

from app.services.process_runner import ProcessExecutionError, run_process, safe_process_detail


TEXT_CODECS = {"subrip": "srt", "ass": "ass", "ssa": "ssa", "webvtt": "vtt", "mov_text": "txt"}
VOBSUB_HEADER = "# VobSub index file,"
VOBSUB_ID = re.compile(r"^id:\s*[^,\r\n]+,\s*index:\s*\d+\s*$", re.MULTILINE)
VOBSUB_TIMESTAMP = re.compile(
    r"^timestamp:\s*\d{2}:\d{2}:\d{2}:\d{3},\s*filepos:\s*[0-9a-fA-F]+\s*$", re.MULTILINE
)


@dataclass(frozen=True)
class SubtitleExtractionResult:
    files: list[Path]
    warnings: list[str]


def _validate_files(paths: list[Path]) -> None:
    if any(not path.is_file() or path.stat().st_size == 0 for path in paths):
        raise RuntimeError("Nie utworzono kompletnej referencji napisów")


def _validate_vobsub(index_path: Path, sub_path: Path) -> None:
    _validate_files([index_path, sub_path])
    if index_path.stem != sub_path.stem or index_path.name != f"{sub_path.stem}.idx":
        raise RuntimeError("Pliki VobSub IDX i SUB nie mają wspólnej nazwy bazowej")
    content = index_path.read_text(encoding="utf-8", errors="replace")
    if not content.startswith(VOBSUB_HEADER) or not VOBSUB_ID.search(content) or not VOBSUB_TIMESTAMP.search(content):
        raise RuntimeError("Plik IDX nie zawiera prawidłowego indeksu VobSub")


async def extract_subtitle(reference: dict, media_path: Path, target: Path, timeout: float,
                           basename: str = "selected", keep_text_original: bool = True) -> SubtitleExtractionResult:
    target.mkdir(parents=True, exist_ok=True)
    index, codec = int(reference["streamIndex"]), reference.get("codec")
    subtitle_type = reference.get("type")
    prefix = f"{basename}.eng" if basename == "selected" else basename
    warnings: list[str] = []

    if subtitle_type == "text":
        outputs: list[Path] = []
        if keep_text_original:
            extension = TEXT_CODECS.get(codec, "txt")
            original = target / f"{basename}.original.{extension}"
            await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}",
                               "-c:s", "copy", "-y", str(original)], timeout)
            outputs.append(original)
        converted = target / f"{prefix}.srt"
        await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}",
                           "-c:s", "srt", "-y", str(converted)], timeout)
        outputs.append(converted)
    elif codec == "hdmv_pgs_subtitle":
        outputs = [target / f"{prefix}.sup"]
        await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}",
                           "-c", "copy", "-y", str(outputs[0])], timeout)
    elif codec == "dvd_subtitle":
        index_path, sub_path = target / f"{prefix}.idx", target / f"{prefix}.sub"
        temporary = target / f".{basename}.reference.tmp.mks"
        try:
            await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}",
                               "-c", "copy", "-f", "matroska", "-y", str(temporary)], timeout)
            result = await run_process(["mkvextract", str(temporary), "tracks", f"0:{index_path}"], timeout,
                                       accepted_returncodes=(0, 1))
            _validate_vobsub(index_path, sub_path)
            if result.returncode == 1:
                warnings.append(
                    f"mkvextract zakończył ekstrakcję z ostrzeżeniem: "
                    f"{safe_process_detail(result.stderr or result.stdout)}"
                )
        except (ProcessExecutionError, RuntimeError) as exc:
            raise ProcessExecutionError(f"Nie udało się wyeksportować ścieżki DVD/VobSub. {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        outputs = [index_path, sub_path]
    elif codec == "dvb_subtitle":
        outputs = [target / f"{prefix}.mks"]
        await run_process(["ffmpeg", "-v", "error", "-i", str(media_path), "-map", f"0:{index}",
                           "-c", "copy", "-y", str(outputs[0])], timeout)
    else:
        return SubtitleExtractionResult([], [f"Nieobsługiwany kodek napisów: {codec or 'nieznany'}"])

    _validate_files(outputs)
    return SubtitleExtractionResult(outputs, warnings)
