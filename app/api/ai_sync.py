import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.services.ai_sync import (ApiSettings, AiSyncError, SYNC_INSTRUCTION, chat_request,
                                  read_cues, segments, validate_result)
from app.services.alignment import sha256, write_preview
from app.services.artifact_retention import validated_job_directory

router = APIRouter(tags=["ai-sync"])


def error(message: str, status: int = 422):
    return HTTPException(status, detail={"code": "AI_SYNC_ERROR", "message": message})


@router.get("/api/settings/ai")
async def get_settings(request: Request):
    return request.app.state.ai_settings.get().public()


@router.put("/api/settings/ai")
async def save_settings(payload: ApiSettings, request: Request):
    return request.app.state.ai_settings.save(payload).public()


@router.post("/api/settings/ai/test")
async def test_connection(request: Request):
    try:
        result, elapsed, usage = await chat_request(request.app.state.ai_settings.get(),
            'Return only this JSON object: {"ok":true}', {"test": "connection"})
        if result != {"ok": True}:
            raise AiSyncError("Model odpowiedział, ale nie zachował formatu testowego JSON")
        return {"ok": True, "elapsed_seconds": elapsed, "usage": usage}
    except AiSyncError as exc:
        raise error(str(exc), 502) from exc


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    polish_file: str
    reference_source_id: str


def prepared(request: Request, job_id: str):
    job = request.app.state.jobs.get(job_id)
    if not job or job.get("job_type") != "PREPARE_WORKPACK":
        raise error("Nie znaleziono zadania", 404)
    report = job.get("report") or {}
    if job["status"] != "WORKPACK_READY" or report.get("pipeline") != "PREPARE_SYNC":
        raise error("Najpierw przygotuj workpack do synchronizacji")
    if report.get("externalReferenceConfirmationRequired") or report.get("requiresOcr"):
        raise error("Najpierw zatwierdź referencję EN i zakończ ekstrakcję/OCR")
    root = request.app.state.settings.data_root / "work" / "jobs"
    directory = validated_job_directory(root, root / job_id)
    if directory is None or (report.get("workpack") or {}).get("artifactExpired"):
        raise error("Pliki zadania wygasły; przygotuj je ponownie", 410)
    return directory, report


def input_file(directory: Path, entry: dict, name_key: str) -> Path:
    name = entry.get(name_key) or ""
    path = directory / name
    if (not name or Path(name).is_absolute() or ".." in Path(name).parts
            or not path.resolve().is_relative_to(directory.resolve())
            or any(part.is_symlink() for part in (path, *path.parents) if part != directory.parent)
            or path.suffix.lower() != ".srt" or not path.is_file()
            or sha256(path) != entry.get("sha256")):
        raise error("Plik napisów jest niedostępny lub zmienił się; przygotuj workpack ponownie")
    return path


def manifest(directory: Path):
    path = directory / "manifest.json"
    if not path.is_file() or path.is_symlink():
        raise error("Brak manifestu przygotowanych napisów")
    return json.loads(path.read_text(encoding="utf-8"))


def saved_result(directory: Path):
    path = directory / "ai-sync.json"
    if not path.is_file() or path.is_symlink():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if result["manifest_sha256"] != sha256(directory / "manifest.json"):
        return None
    for entry in result["inputs"]:
        input_file(directory, entry, "name")
    output = directory / "ai-sync.pl.srt"
    if not output.is_file() or output.is_symlink() or sha256(output) != result["sha256"]:
        return None
    return result


@router.post("/api/tasks/{job_id}/ai-sync")
async def synchronize(job_id: str, payload: SyncRequest, request: Request):
    prepared(request, job_id)
    locks = request.app.state.ai_sync_locks
    lock = locks.setdefault(job_id, asyncio.Lock())
    if lock.locked():
        raise error("Synchronizacja tego zadania już trwa", 409)
    async with lock:
        directory, report = prepared(request, job_id)
        data = manifest(directory)
        reference = data.get("reference") or {}
        selected = report.get("selectedEnglish") or {}
        selected_id = (f"external:{selected.get('name')}" if selected.get("sourceType") == "external"
                       else f"embedded:{selected.get('streamIndex')}")
        if payload.reference_source_id != selected_id:
            raise error("Wybrano inne EN; najpierw zbuduj ponownie workpack z tą referencją")
        english_entries = [entry for entry in reference.get("files", [])
                           if Path(entry.get("name", "")).name in {"selected.eng.srt", "selected.eng.ocr.srt"}]
        polish_entries = [entry for entry in data.get("polish_candidates", [])
                          if entry.get("archiveName") == payload.polish_file]
        if len(english_entries) != 1 or len(polish_entries) != 1:
            raise error("Wybierz przygotowaną referencję EN i polski plik SRT")
        english_path = input_file(directory, english_entries[0], "name")
        polish_path = input_file(directory, polish_entries[0], "archiveName")
        digest = sha256(directory / "manifest.json")
        duration = data.get("media", {}).get("duration_ms")
        if type(duration) is not int or duration <= 0:
            raise error("Brak poprawnego czasu trwania filmu")
        # Remove the previous result before a new attempt, including invalid responses.
        for name in ("ai-sync.json", "ai-sync.pl.srt"):
            (directory / name).unlink(missing_ok=True)
        try:
            english, polish = read_cues(english_path, "en"), read_cues(polish_path, "pl")
            settings = request.app.state.ai_settings.get()
            result, elapsed, usage = await chat_request(settings, SYNC_INSTRUCTION,
                {"duration_ms": duration, "english": segments(english), "polish": segments(polish)})
            output = validate_result(result, polish, duration)
            prepared(request, job_id)
            if digest != sha256(directory / "manifest.json"):
                raise AiSyncError("Referencja zmieniła się podczas żądania; przygotuj dane ponownie")
            input_file(directory, english_entries[0], "name")
            input_file(directory, polish_entries[0], "archiveName")
            target = directory / "ai-sync.pl.srt"
            write_preview(output, target)
            summary = {"model": settings.model, "elapsed_seconds": elapsed, "usage": usage,
                       "cue_count": len(output), "manifest_sha256": digest, "sha256": sha256(target),
                       "inputs": [{"name": entry[name_key], "sha256": entry["sha256"]}
                                  for entry, name_key in ((english_entries[0], "name"),
                                                          (polish_entries[0], "archiveName"))]}
            temporary = directory / ".ai-sync.json.tmp"
            temporary.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
            temporary.replace(directory / "ai-sync.json")
            return summary
        except AiSyncError as exc:
            raise error(str(exc), 502) from exc


@router.get("/api/tasks/{job_id}/ai-sync")
async def result(job_id: str, request: Request):
    directory, _ = prepared(request, job_id)
    return {"result": saved_result(directory)}


@router.get("/api/tasks/{job_id}/ai-sync/download")
async def download(job_id: str, request: Request):
    directory, _ = prepared(request, job_id)
    if not saved_result(directory):
        raise error("Brak poprawnego wyniku synchronizacji", 404)
    return FileResponse(directory / "ai-sync.pl.srt", media_type="application/x-subrip",
                        filename="AI-Synced.pl.srt")
