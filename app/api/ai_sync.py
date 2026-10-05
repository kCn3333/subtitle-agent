import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.services.ai_sync import (ApiSettings, AiSyncError, SYNC_INSTRUCTION, chat_request,
                                  read_cues, segments, validate_result, TRANSLATION_INSTRUCTION, validate_translation)
from app.services.alignment import sha256, write_preview
from app.services.ai_console import capture
from app.services.artifact_retention import validated_job_directory

router = APIRouter(tags=["ai-sync"])


def error(message: str, status: int = 422, elapsed_seconds=None, usage=None):
    detail = {"code": "AI_SYNC_ERROR", "message": message}
    if elapsed_seconds is not None:
        detail['elapsed_seconds'] = elapsed_seconds
    if usage is not None:
        detail['usage'] = usage
        detail['total_cost'] = usage.get('cost')
    return HTTPException(status, detail=detail)


@router.get("/api/settings/ai")
async def get_settings(request: Request):
    return request.app.state.ai_settings.get().public()


@router.get("/api/settings/ai/health")
async def api_health(request: Request):
    return await request.app.state.ai_health.check(request.app.state.ai_settings.get())


@router.put("/api/settings/ai")
async def save_settings(payload: ApiSettings, request: Request):
    return request.app.state.ai_settings.save(payload).public()


@router.post("/api/settings/ai/test")
async def test_connection(request: Request):
    settings = request.app.state.ai_settings.get()
    try:
        with capture(request.app.state.ai_console, settings, "TEST"):
            result, elapsed, usage = await chat_request(settings,
                'Return only this JSON object: {"ok":true}', {"test": "connection"})
            if result != {"ok": True}:
                raise AiSyncError("Model odpowiedział, ale nie zachował formatu testowego JSON")
            return {"ok": True, "elapsed_seconds": elapsed, "usage": usage}
    except AiSyncError as exc:
        raise error(str(exc), 502, exc.elapsed_seconds, exc.usage) from exc


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    polish_file: str | None = None
    reference_source_id: str


def prepared(request: Request, job_id: str, pipeline: str | None = None):
    job = request.app.state.jobs.get(job_id)
    if not job or job.get("job_type") != "PREPARE_WORKPACK":
        raise error("Nie znaleziono zadania", 404)
    report = job.get("report") or {}
    if (job["status"] != "WORKPACK_READY" or report.get("pipeline") not in {"PREPARE_SYNC", "PREPARE_TRANSLATION"}
            or (pipeline and report.get("pipeline") != pipeline)):
        raise error("Najpierw przygotuj workpack dla wybranego trybu AI")
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


def saved_result(directory: Path, stem="ai-sync"):
    path = directory / f"{stem}.json"
    if not path.is_file() or path.is_symlink():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if result["manifest_sha256"] != sha256(directory / "manifest.json"):
        return None
    for entry in result["inputs"]:
        input_file(directory, entry, "name")
    output = directory / f"{stem}.pl.srt"
    if not output.is_file() or output.is_symlink() or sha256(output) != result["sha256"]:
        return None
    return result


@router.post("/api/tasks/{job_id}/ai-sync")
async def synchronize(job_id: str, payload: SyncRequest, request: Request):
    return await process(job_id, payload, request, "PREPARE_SYNC")


@router.post("/api/tasks/{job_id}/ai-translate")
async def translate(job_id: str, payload: SyncRequest, request: Request):
    return await process(job_id, payload, request, "PREPARE_TRANSLATION")


@router.get("/api/tasks/{job_id}/ai-status")
async def operation_status(job_id: str, request: Request):
    if not request.app.state.jobs.get(job_id):
        raise error("Nie znaleziono zadania", 404)
    return request.app.state.ai_operations.get(job_id, {"status":"idle"})


async def process(job_id, payload, request, pipeline):
    prepared(request, job_id, pipeline)
    lock = request.app.state.ai_sync_locks.setdefault(job_id, asyncio.Lock())
    if lock.locked():
        exc = error("Operacja AI tego zadania już trwa", 409)
        exc.detail['code'] = 'AI_OPERATION_RUNNING'
        raise exc
    async with lock:
        states = request.app.state.ai_operations
        for expired in list(states):
            if not request.app.state.jobs.get(expired):
                states.pop(expired, None)
        state = {"status":"running", "operation_id":str(uuid.uuid4()),
                 "started_at":datetime.now(timezone.utc).isoformat(),
                 "mode":"translation" if pipeline == "PREPARE_TRANSLATION" else "sync",
                 "title":request.app.state.jobs.get(job_id)['display_title'],
                 "polish_file":payload.polish_file, "phase":"Przygotowanie danych dla modelu"}
        states[job_id] = state
        try:
            result = await execute(job_id, payload, request, pipeline)
            result = {**result, "operation_id":state['operation_id']}
            state.update(status="completed", result=result, phase="Gotowe")
            return result
        except HTTPException as exc:
            if isinstance(exc.detail, dict):
                exc.detail['operation_id'] = state['operation_id']
            state.update(status="failed", error=exc.detail, phase="Operacja zakończona błędem")
            raise
        except BaseException:
            state.update(status="failed", error={"message":"Operacja AI została przerwana"}, phase="Operacja przerwana")
            raise


async def execute(job_id, payload, request, pipeline):
    translation = pipeline == "PREPARE_TRANSLATION"
    stem = "ai-translation" if translation else "ai-sync"
    directory, report = prepared(request, job_id, pipeline)
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
    if len(english_entries) != 1:
        raise error("Brak przygotowanej tekstowej referencji EN; zakończ ekstrakcję/OCR")
    if not translation and len(polish_entries) != 1:
        raise error("Wybierz przygotowany polski plik SRT")
    english_path = input_file(directory, english_entries[0], "name")
    polish_path = None if translation else input_file(directory, polish_entries[0], "archiveName")
    inputs = [(english_entries[0], "name")]
    if not translation:
        inputs.append((polish_entries[0], "archiveName"))
    digest = sha256(directory / "manifest.json")
    duration = data.get("media", {}).get("duration_ms")
    if type(duration) is not int or duration <= 0:
        raise error("Brak poprawnego czasu trwania filmu")
    # Remove the previous result before a new attempt, including invalid responses.
    for name in (f"{stem}.json", f"{stem}.pl.srt"):
        (directory / name).unlink(missing_ok=True)
    settings = request.app.state.ai_settings.get()
    request.app.state.jobs.artifact_users.add(job_id)
    elapsed, usage = None, None
    try:
        with capture(request.app.state.ai_console, settings, "TRANSLATE" if translation else "SYNC", job_id,
                     request.app.state.jobs.get(job_id)["display_title"]):
            english = read_cues(english_path, "en")
            data = {"duration_ms": duration, "english": segments(english)}
            polish = read_cues(polish_path, "pl") if polish_path else None
            if polish is not None:
                data['polish'] = segments(polish)
            request.app.state.ai_operations[job_id]["phase"] = "Oczekiwanie na odpowiedź modelu"
            result, elapsed, usage = await chat_request(settings,
                TRANSLATION_INSTRUCTION if translation else SYNC_INSTRUCTION, data)
            request.app.state.ai_operations[job_id]["phase"] = "Sprawdzanie odpowiedzi modelu"
            output = (validate_translation(result, english, duration) if translation
                      else validate_result(result, polish, duration))
            prepared(request, job_id, pipeline)
            if digest != sha256(directory / "manifest.json"):
                raise AiSyncError("Referencja zmieniła się podczas żądania; przygotuj dane ponownie")
            for entry, name_key in inputs:
                input_file(directory, entry, name_key)
            request.app.state.ai_operations[job_id]["phase"] = "Zapisywanie napisów SRT"
            target = directory / f"{stem}.pl.srt"
            write_preview(output, target)
            summary = {"model": settings.model, "elapsed_seconds": elapsed, "usage": usage,
                       "mode": "translation" if translation else "sync",
                       "total_cost": (usage or {}).get("cost"),
                       "cue_count": len(output), "manifest_sha256": digest, "sha256": sha256(target),
                       "inputs": [{"name": entry[name_key], "sha256": entry["sha256"]}
                                  for entry, name_key in inputs]}
            temporary = directory / f".{stem}.json.tmp"
            temporary.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
            temporary.replace(directory / f"{stem}.json")
            with request.app.state.jobs._lock:
                request.app.state.jobs.archive.capture(request.app.state.jobs.get(job_id))
            return summary
    except AiSyncError as exc:
        raise error(str(exc), 502, exc.elapsed_seconds if exc.elapsed_seconds is not None else elapsed,
                    exc.usage if exc.usage is not None else usage) from exc
    finally:
        request.app.state.jobs.artifact_users.discard(job_id)
        request.app.state.jobs.cleanup_expired_artifacts()

@router.get("/api/tasks/{job_id}/ai-sync")
async def result(job_id: str, request: Request):
    directory, _ = prepared(request, job_id, "PREPARE_SYNC")
    return {"result": saved_result(directory)}


@router.get("/api/tasks/{job_id}/ai-sync/download")
async def download(job_id: str, request: Request):
    directory, _ = prepared(request, job_id, "PREPARE_SYNC")
    if not saved_result(directory):
        raise error("Brak poprawnego wyniku synchronizacji", 404)
    return FileResponse(directory / "ai-sync.pl.srt", media_type="application/x-subrip",
                        filename="AI-Synced.pl.srt")


@router.get("/api/tasks/{job_id}/ai-translate")
async def translation_result(job_id: str, request: Request):
    directory, _ = prepared(request, job_id, "PREPARE_TRANSLATION")
    return {"result": saved_result(directory, "ai-translation")}


@router.get("/api/tasks/{job_id}/ai-translate/download")
async def translation_download(job_id: str, request: Request):
    directory, _ = prepared(request, job_id, "PREPARE_TRANSLATION")
    if not saved_result(directory, "ai-translation"):
        raise error("Brak poprawnego wyniku tłumaczenia", 404)
    return FileResponse(directory / "ai-translation.pl.srt", media_type="application/x-subrip",
                        filename="AI-Translated.pl.srt")


@router.get("/api/settings/ai/console")
async def ai_console(request: Request):
    return {"entries": request.app.state.ai_console.read()}


@router.delete("/api/settings/ai/console")
async def clear_ai_console(request: Request):
    request.app.state.ai_console.clear()
    return {"ok": True}
