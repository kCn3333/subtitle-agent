from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.models.job import CreateTaskRequest, PrepareWorkpackRequest, RebuildWorkpackRequest, WorkpackTaskType
from app.services.media_analysis import UserInputError
from app.services.ocr_client import worker_available
from app.services.workpack import sha256_file

router = APIRouter(prefix="/api/workpacks", tags=["workpacks"])
tasks_router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def missing() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "WORKPACK_NOT_FOUND", "message": "Nie znaleziono workpacka"})


@router.get("/config")
async def config(request: Request) -> dict:
    settings = request.app.state.settings
    return {"appMode": settings.subtitle_agent_app_mode, "schemaVersion": "subtitle-workpack-v2",
            "referenceScoreMargin": settings.workpack_reference_score_margin,
            "maxReferenceAlternatives": settings.workpack_max_reference_alternatives,
            "maxPolishCandidates": settings.workpack_max_polish_candidates,
            "maxArchiveBytes": settings.workpack_max_archive_bytes, "maxFiles": settings.workpack_max_files,
            "ocrWorkerEnabled": settings.ocr_worker_url is not None}


@router.get("/ocr-health")
async def ocr_health(request: Request) -> dict:
    worker_url = request.app.state.settings.ocr_worker_url
    return {"configured": worker_url is not None,
            "available": await worker_available(worker_url)}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_workpack(payload: PrepareWorkpackRequest, request: Request) -> dict:
    job = await request.app.state.jobs.create_workpack(payload.media_path, payload.task_type)
    return {"jobId": job["id"], "status": job["status"], "jobType": "PREPARE_WORKPACK", "taskType": payload.task_type}


@tasks_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_task(payload: CreateTaskRequest, request: Request) -> dict:
    task_type = WorkpackTaskType(payload.mode.value)
    job = await request.app.state.jobs.create_workpack(payload.media_path, task_type)
    return {"jobId": job["id"], "status": job["status"], "jobType": "PREPARE_WORKPACK", "mode": payload.mode}


@router.get("/{job_id}")
async def report(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.get(job_id)
    if not job or job.get("job_type") != "PREPARE_WORKPACK": raise missing()
    return {"jobId": job["id"], "status": job["status"], "progress": job["progress"],
            "taskType": job.get("task_type"), "createdAt": job["created_at"], "finishedAt": job["finished_at"],
            "report": job.get("report"), "errorMessage": job.get("error_message")}


@tasks_router.get("/{job_id}")
async def task_report(job_id: str, request: Request) -> dict:
    return await report(job_id, request)


@router.post("/{job_id}/reference", status_code=status.HTTP_202_ACCEPTED)
async def rebuild(job_id: str, payload: RebuildWorkpackRequest, request: Request) -> dict:
    try:
        await request.app.state.jobs.rebuild_workpack(job_id, payload.reference_source_id)
    except UserInputError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_REFERENCE", "message": str(exc)}) from exc
    return {"jobId": job_id, "status": "BUILDING_WORKPACK"}


@router.get("/{job_id}/download")
async def download(job_id: str, request: Request) -> FileResponse:
    job = request.app.state.jobs.get(job_id)
    workpack = ((job or {}).get("report") or {}).get("workpack") if job else None
    if not job or job.get("job_type") != "PREPARE_WORKPACK" or not workpack: raise missing()
    if workpack.get("artifactExpired"):
        raise HTTPException(status_code=410, detail={"code": "ARTIFACT_EXPIRED",
                                                     "message": "Artefakt wygasł; raport pozostaje dostępny"})
    job_dir = (request.app.state.settings.data_root / "work" / "jobs" / job_id).resolve()
    archive = Path(workpack.get("path") or "").resolve()
    if not archive.is_relative_to(job_dir) or archive.parent != job_dir or archive.suffix.lower() != ".zip" or not archive.is_file():
        raise missing()
    finished = datetime.fromisoformat(job["finished_at"]) if job.get("finished_at") else None
    if finished is not None and finished.tzinfo is None:
        finished = finished.astimezone()
    expired = finished is not None and finished < datetime.now().astimezone() - timedelta(
        hours=request.app.state.settings.workpack_retention_hours)
    if expired:
        raise HTTPException(status_code=410, detail={"code": "ARTIFACT_EXPIRED",
                                                     "message": "Artefakt wygasł; raport pozostaje dostępny"})
    digest = sha256_file(archive)
    if digest != workpack.get("sha256"): raise HTTPException(
        status_code=409, detail={"code": "WORKPACK_HASH_MISMATCH", "message": "Suma kontrolna workpacka jest niezgodna"})
    return FileResponse(archive, media_type="application/zip", filename=workpack["filename"],
                        headers={"X-Workpack-SHA256": digest})


@tasks_router.get("/{job_id}/download")
async def task_download(job_id: str, request: Request) -> FileResponse:
    return await download(job_id, request)
