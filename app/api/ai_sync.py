import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.services.ai_sync import (ApiSettings, AiSyncError, SYNC_INSTRUCTION, chat_request,
                                  read_cues, segments, validate_result, TRANSLATION_INSTRUCTION, validate_translation)
from app.services.alignment import sha256, write_preview
from app.services.ai_console import capture
from app.services.artifact_retention import validated_job_directory
from app.services.operation_queue import JobBusy
from app.services.directory_ai import (DirectoryRequest, describe_inputs, process_directory,
                                        directory_result, result_path, settings_digest)
from app.services.workpack import safe_filename

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


@router.post("/api/settings/ai/test", status_code=202)
async def test_connection(request: Request):
    settings = request.app.state.ai_settings.get().model_copy(deep=True)
    return request.app.state.ai_queue.submit({'mode':'test', 'title':'Test połączenia API'},
                                           lambda: execute_test(request, settings))


@router.get("/api/ai-operations/{operation_id}")
async def queued_operation(operation_id: str, request: Request):
    state = request.app.state.ai_queue.status(operation_id)
    if state is None:
        raise error('Nie znaleziono operacji AI', 404)
    return state


async def execute_test(request: Request, settings):
    try:
        with capture(request.app.state.ai_console, settings, "TEST"):
            result, elapsed, usage = await chat_request(settings,
                'Return only this JSON object: {"ok":true}', {"test": "connection"})
            if result.get("ok") is not True:
                raise AiSyncError("Model odpowiedział, ale JSON testowy nie zawiera ok: true", elapsed, usage)
            return {"ok": True, "elapsed_seconds": elapsed, "usage": usage}
    except AiSyncError as exc:
        raise error(str(exc), 502, exc.elapsed_seconds, exc.usage) from exc


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    polish_file: str | None = None
    reference_source_id: str


def prepared(request: Request, job_id: str, pipeline: str | None = None, batch_parent: str | None = None):
    job = request.app.state.jobs.get(job_id)
    if not job or job.get("job_type") != "PREPARE_WORKPACK":
        raise error("Nie znaleziono zadania", 404)
    if batch_parent and job.get('batch_parent_id') != batch_parent:
        raise error('Plik nie należy do wybranego katalogu')
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


@router.post("/api/tasks/{job_id}/ai-sync", status_code=202)
async def synchronize(job_id: str, payload: SyncRequest | DirectoryRequest, request: Request):
    return await process(job_id, payload, request, "PREPARE_SYNC")


@router.post("/api/tasks/{job_id}/ai-translate", status_code=202)
async def translate(job_id: str, payload: SyncRequest | DirectoryRequest, request: Request):
    return await process(job_id, payload, request, "PREPARE_TRANSLATION")


@router.get("/api/tasks/{job_id}/ai-status")
async def operation_status(job_id: str, request: Request):
    if not request.app.state.jobs.get(job_id):
        raise error("Nie znaleziono zadania", 404)
    state = request.app.state.ai_operations.get(job_id)
    return request.app.state.ai_queue.status(state['operation_id']) if state else {"status":"idle"}


@router.get('/api/tasks/{job_id}/ai-inputs')
async def directory_inputs(job_id: str, request: Request):
    return describe_inputs(request, job_id)


async def process(job_id, payload, request, pipeline):
    if isinstance(payload, DirectoryRequest):
        return await process_directory(request, job_id, payload, pipeline)
    queue = request.app.state.ai_queue
    intent = {'pipeline':pipeline, **payload.model_dump()}
    duplicate = queue.existing(job_id, intent)
    if duplicate:
        return duplicate
    if job_id in request.app.state.jobs.reserved_jobs:
        raise JobBusy('Zadanie jest już w kolejce lub jest przetwarzane.')
    # Validate before admission, then revalidate when the worker starts.
    directory, *_ = validate_inputs(job_id, payload, request, pipeline)
    expected_manifest = sha256(directory / "manifest.json")
    settings = request.app.state.ai_settings.get().model_copy(deep=True)
    submitted = payload.model_copy(deep=True)
    state = {'job_id':job_id, 'request':intent, 'mode':'translation' if pipeline == 'PREPARE_TRANSLATION' else 'sync',
             'title':request.app.state.jobs.get(job_id)['display_title'], 'polish_file':payload.polish_file}
    return queue.submit(state, lambda: execute(job_id, submitted, request, pipeline, settings, expected_manifest))


def validate_inputs(job_id, payload, request, pipeline, batch_parent=None):
    directory, report = prepared(request, job_id, pipeline, batch_parent)
    data = manifest(directory)
    reference = data.get('reference') or {}
    selected = report.get('selectedReference') or report.get('selectedEnglish') or {}
    selected_id = (f"external:{selected.get('name')}" if selected.get('sourceType') == 'external'
                   else f"embedded:{selected.get('streamIndex')}")
    if payload.reference_source_id != selected_id:
        raise error('Wybrano inną referencję; najpierw zbuduj ponownie workpack z tą referencją')
    english_entries = [entry for entry in reference.get('files', [])
                       if Path(entry.get('name', '')).name in {'selected.eng.srt','selected.eng.ocr.srt','selected.ref.srt','selected.ref.ocr.srt'}]
    polish_entries = [entry for entry in data.get('polish_candidates', []) if entry.get('archiveName') == payload.polish_file]
    if len(english_entries) != 1:
        raise error('Brak przygotowanej tekstowej referencji; zakończ ekstrakcję/OCR')
    if pipeline != 'PREPARE_TRANSLATION' and len(polish_entries) != 1:
        raise error('Wybierz przygotowany polski plik SRT')
    input_file(directory, english_entries[0], 'name')
    if pipeline != 'PREPARE_TRANSLATION':
        input_file(directory, polish_entries[0], 'archiveName')
    duration = data.get('media', {}).get('duration_ms')
    if type(duration) is not int or duration <= 0:
        raise error('Brak poprawnego czasu trwania filmu')
    return directory, report, data, reference, selected, english_entries, polish_entries


def set_phase(request, job_id, phase, batch_parent=None):
    state = request.app.state.ai_operations[batch_parent or job_id]
    prefix = f"{state['current_index'] + 1}/{len(state['items'])} · {state['current_item']} · " if batch_parent else ''
    state['phase'] = prefix + phase


async def execute(job_id, payload, request, pipeline, settings, expected_manifest, batch_parent=None):
    translation = pipeline == "PREPARE_TRANSLATION"
    stem = "ai-translation" if translation else "ai-sync"
    directory, report, data, reference, selected, english_entries, polish_entries = validate_inputs(job_id, payload, request, pipeline, batch_parent)
    if sha256(directory / 'manifest.json') != expected_manifest:
        raise error('Dane workpacka zmieniły się podczas oczekiwania. Przygotuj go ponownie.', 409)
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
    elapsed, usage = None, None
    try:
        with capture(request.app.state.ai_console, settings, "TRANSLATE" if translation else "SYNC", batch_parent or job_id,
                     request.app.state.jobs.get(job_id)["display_title"]):
            language = reference.get("language") or selected.get("language") or "eng"
            english = read_cues(english_path, "en" if language in {"eng", "en", "english"} else "ref")
            data = {"duration_ms": duration, "reference_language": language,
                    "english" if english[0].cue_id.startswith("en:") else "reference": segments(english)}
            polish = read_cues(polish_path, "pl") if polish_path else None
            if polish is not None:
                data['polish'] = segments(polish)
            set_phase(request, job_id, 'Oczekiwanie na odpowiedź modelu', batch_parent)
            result, elapsed, usage = await chat_request(settings,
                TRANSLATION_INSTRUCTION if translation else SYNC_INSTRUCTION, data)
            set_phase(request, job_id, 'Sprawdzanie odpowiedzi modelu', batch_parent)
            output = (validate_translation(result, english, duration) if translation
                      else validate_result(result, polish, duration))
            prepared(request, job_id, pipeline, batch_parent)
            if digest != sha256(directory / "manifest.json"):
                raise AiSyncError("Referencja zmieniła się podczas żądania; przygotuj dane ponownie")
            for entry, name_key in inputs:
                input_file(directory, entry, name_key)
            set_phase(request, job_id, 'Zapisywanie napisów SRT', batch_parent)
            target = directory / f"{stem}.pl.srt"
            write_preview(output, target)
            output_digest = sha256(target)
            if batch_parent:
                media_name = safe_filename(Path(request.app.state.jobs.get(job_id)['media_path']).stem)
                filename = f"{media_name}.{'AI-Translated' if translation else 'AI-Synced'}-v001.pl.srt"
            else:
                with request.app.state.jobs._lock:
                    filename = request.app.state.jobs.archive.record(request.app.state.jobs.get(job_id), target,
                        'translation' if translation else 'synchronization', output_digest)
            if filename is None:
                raise AiSyncError("Nie udało się zarchiwizować wyniku SRT")
            summary = {"model": settings.model, "elapsed_seconds": elapsed, "usage": usage,
                       "mode": "translation" if translation else "sync",
                       "total_cost": (usage or {}).get("cost"),
                       "cue_count": len(output), "manifest_sha256": digest, "sha256": output_digest,
                       "filename": filename,
                       "inputs": [{"name": entry[name_key], "sha256": entry["sha256"]}
                                  for entry, name_key in inputs]}
            if batch_parent:
                summary['settings_sha256'] = settings_digest(settings)
            temporary = directory / f".{stem}.json.tmp"
            temporary.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
            temporary.replace(directory / f"{stem}.json")
            if not batch_parent:
                with request.app.state.jobs._lock:
                    request.app.state.jobs.archive.capture(request.app.state.jobs.get(job_id))
            return summary
    except AiSyncError as exc:
        raise error(str(exc), 502, exc.elapsed_seconds if exc.elapsed_seconds is not None else elapsed,
                    exc.usage if exc.usage is not None else usage) from exc

@router.get("/api/tasks/{job_id}/ai-sync")
async def result(job_id: str, request: Request):
    if (request.app.state.jobs.get(job_id) or {}).get('job_type') == 'DIRECTORY_WORKPACK':
        return {'result': directory_result(request, job_id, 'PREPARE_SYNC')}
    directory, _ = prepared(request, job_id, "PREPARE_SYNC")
    return {"result": named_result(request, job_id, directory)}


def named_result(request, job_id, directory, stem='ai-sync'):
    result = saved_result(directory, stem)
    if result and not result.get('filename'):
        kind = 'translation' if stem == 'ai-translation' else 'synchronization'
        result['filename'] = request.app.state.jobs.archive.result_filename(
            request.app.state.jobs.get(job_id), kind, result['sha256'])
    return result


@router.get("/api/tasks/{job_id}/ai-sync/download")
async def download(job_id: str, request: Request):
    if (request.app.state.jobs.get(job_id) or {}).get('job_type') == 'DIRECTORY_WORKPACK':
        result = directory_result(request, job_id, 'PREPARE_SYNC')
        if not result:
            raise error('Brak wyniku AI katalogu', 404)
        return FileResponse(result_path(request, job_id, result), media_type='application/zip', filename=result['filename'])
    directory, _ = prepared(request, job_id, "PREPARE_SYNC")
    result = named_result(request, job_id, directory)
    if not result:
        raise error("Brak poprawnego wyniku synchronizacji", 404)
    return FileResponse(directory / "ai-sync.pl.srt", media_type="application/x-subrip",
                        filename=result['filename'])


@router.get("/api/tasks/{job_id}/ai-translate")
async def translation_result(job_id: str, request: Request):
    if (request.app.state.jobs.get(job_id) or {}).get('job_type') == 'DIRECTORY_WORKPACK':
        return {'result': directory_result(request, job_id, 'PREPARE_TRANSLATION')}
    directory, _ = prepared(request, job_id, "PREPARE_TRANSLATION")
    return {"result": named_result(request, job_id, directory, "ai-translation")}


@router.get("/api/tasks/{job_id}/ai-translate/download")
async def translation_download(job_id: str, request: Request):
    if (request.app.state.jobs.get(job_id) or {}).get('job_type') == 'DIRECTORY_WORKPACK':
        result = directory_result(request, job_id, 'PREPARE_TRANSLATION')
        if not result:
            raise error('Brak wyniku AI katalogu', 404)
        return FileResponse(result_path(request, job_id, result), media_type='application/zip', filename=result['filename'])
    directory, _ = prepared(request, job_id, "PREPARE_TRANSLATION")
    result = named_result(request, job_id, directory, "ai-translation")
    if not result:
        raise error("Brak poprawnego wyniku tłumaczenia", 404)
    return FileResponse(directory / "ai-translation.pl.srt", media_type="application/x-subrip",
                        filename=result['filename'])


@router.get("/api/settings/ai/console")
async def ai_console(request: Request):
    return {"entries": request.app.state.ai_console.read()}


@router.delete("/api/settings/ai/console")
async def clear_ai_console(request: Request):
    request.app.state.ai_console.clear()
    return {"ok": True}
