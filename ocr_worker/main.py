import asyncio
import base64
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request


app = FastAPI(title="Subtitle OCR Worker", docs_url=None, redoc_url=None)
_semaphore = asyncio.Semaphore(1)
SECONV = os.getenv("SECONV_BIN", "/opt/seconv/seconv")
MAX_INPUT_BYTES = int(os.getenv("OCR_MAX_INPUT_BYTES", str(100 * 1024 * 1024)))
MAX_OUTPUT_BYTES = int(os.getenv("OCR_MAX_OUTPUT_BYTES", str(20 * 1024 * 1024)))
TIMEOUT_SECONDS = float(os.getenv("OCR_TIMEOUT_SECONDS", "900"))
ALLOWED_INPUTS = {"selected.eng.idx", "selected.eng.sub", "selected.eng.sup"}


def _timestamp_ms(value: str) -> int:
    hours, minutes, rest = value.replace(",", ".").split(":", 2)
    seconds, milliseconds = rest.split(".", 1)
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(milliseconds[:3])


def _srt_summary(content: bytes) -> dict:
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("OCR nie utworzył prawidłowego UTF-8 SRT") from exc
    blocks = [block for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    cues = []
    for block in blocks:
        lines = block.splitlines()
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            raise ValueError("OCR utworzył uszkodzony segment SRT bez timestampu")
        start, end = (part.strip() for part in lines[timing_index].split("-->", 1))
        try:
            cues.append((_timestamp_ms(start), _timestamp_ms(end), "\n".join(lines[timing_index + 1:]).strip()))
        except (ValueError, IndexError):
            raise ValueError("OCR utworzył nieprawidłowy timestamp SRT") from None
    if not cues:
        raise ValueError("OCR nie utworzył prawidłowych segmentów SRT")
    if any(end < start for start, end, _ in cues):
        raise ValueError("OCR utworzył odwrócony przedział czasu SRT")
    if any(cues[index][0] < cues[index - 1][0] for index in range(1, len(cues))):
        raise ValueError("Timestampy OCR nie są uporządkowane")
    return {"cueCount": len(cues), "emptyCueCount": sum(not cue[2] for cue in cues),
            "firstMs": cues[0][0], "lastStartMs": cues[-1][0], "lastMs": cues[-1][1]}


def _extract_input(payload: bytes, target: Path) -> Path:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if not names or len(names) > 3 or any(Path(name).name != name or name not in ALLOWED_INPUTS for name in names):
                raise ValueError("Archiwum zawiera niedozwolone pliki")
            total = sum(item.file_size for item in archive.infolist())
            if total > MAX_INPUT_BYTES:
                raise ValueError("Rozpakowane pliki przekraczają limit")
            archive.extractall(target)
    except zipfile.BadZipFile as exc:
        raise ValueError("Wejście nie jest prawidłowym ZIP-em") from exc
    sub, index, sup = target / "selected.eng.sub", target / "selected.eng.idx", target / "selected.eng.sup"
    if sub.exists() or index.exists():
        if not sub.is_file() or not index.is_file() or not sub.stat().st_size or not index.stat().st_size:
            raise ValueError("Referencja VobSub wymaga kompletnej pary IDX/SUB")
        return index
    if sup.is_file() and sup.stat().st_size:
        return sup
    raise ValueError("Nie znaleziono obsługiwanej referencji VobSub ani PGS")


async def _run_ocr(source: Path, output: Path, language: str) -> tuple[bytes, str]:
    process = await asyncio.create_subprocess_exec(
        SECONV, str(source), "subrip", "--ocr-engine:tesseract", f"--ocr-language:{language}",
        "--output-filename:selected.eng.ocr.srt", f"--output-folder:{output}",
        "--encoding:utf-8-no-bom", "--overwrite", "--json",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("OCR przekroczył limit czasu")
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        detail = (stderr_text or stdout_text).splitlines()
        raise RuntimeError(f"seconv zakończył się kodem {process.returncode}: "
                           f"{(detail[-1] if detail else 'brak szczegółów')[:300]}")
    try:
        report = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("seconv nie zwrócił maszynowego raportu JSON") from exc
    result = output / "selected.eng.ocr.srt"
    if not result.is_file() or list(output.glob("*.srt")) != [result]:
        raise RuntimeError("seconv nie utworzył oczekiwanego pliku selected.eng.ocr.srt")
    content = result.read_bytes()
    if not content or len(content) > MAX_OUTPUT_BYTES:
        raise RuntimeError("Wynik OCR jest pusty albo przekracza limit")
    warning = stderr_text.splitlines()[-1][:300] if stderr_text else ""
    if isinstance(report, dict) and report.get("errors"):
        raise RuntimeError(f"seconv zgłosił błędy: {str(report['errors'])[:300]}")
    return content, warning


async def _command(*arguments: str) -> str:
    process = await asyncio.create_subprocess_exec(
        *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"Timeout diagnostyki: {Path(arguments[0]).name}")
    if process.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace").strip().splitlines()
        raise RuntimeError((detail[-1] if detail else "Brak szczegółów diagnostyki")[:300])
    return stdout.decode("utf-8", errors="strict").strip()


async def _health_report() -> dict:
    tesseract_version, languages, seconv_version, engines_text = await asyncio.gather(
        _command("tesseract", "--version"),
        _command("tesseract", "--list-langs"),
        _command(SECONV, "--version"),
        _command(SECONV, "list-ocr-engines", "--json"),
    )
    engines = json.loads(engines_text)
    tesseract = next((item for item in engines.get("engines", []) if item.get("id") == "tesseract"), None)
    language_set = {line.strip() for line in languages.splitlines()[1:] if line.strip()}
    ready = bool(tesseract and tesseract.get("ready") is True and "eng" in language_set)
    return {"status": "ok" if ready else "degraded", "engine": "seconv+tesseract", "cpuOnly": True,
            "seconvVersion": seconv_version, "tesseractVersion": tesseract_version.splitlines()[0],
            "languages": sorted(language_set), "tesseractReady": bool(tesseract and tesseract.get("ready") is True)}


@app.get("/health")
async def health() -> dict:
    try:
        return await _health_report()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "degraded", "engine": "seconv+tesseract", "cpuOnly": True,
                "error": str(exc)[:300]}


@app.post("/v1/ocr")
async def ocr(request: Request) -> dict:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/zip":
        raise HTTPException(415, "Wymagany jest application/zip")
    language = request.headers.get("x-ocr-language", "eng").lower()
    if language != "eng":
        raise HTTPException(422, "Worker obsługuje wyłącznie angielskie referencje")
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_INPUT_BYTES:
            raise HTTPException(413, "Wejście jest puste albo przekracza limit")
        chunks.append(chunk)
    payload = b"".join(chunks)
    if not payload:
        raise HTTPException(413, "Wejście jest puste albo przekracza limit")
    async with _semaphore:
        directory = tempfile.mkdtemp(prefix="subtitle-ocr-")
        root = Path(directory)
        try:
            source = _extract_input(payload, root / "input")
            output = root / "output"
            output.mkdir()
            content, warning = await _run_ocr(source, output, language)
            summary = _srt_summary(content)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return {"engine": "seconv+tesseract", "srtBase64": base64.b64encode(content).decode("ascii"),
            "warnings": [warning] if warning else [], **summary}
