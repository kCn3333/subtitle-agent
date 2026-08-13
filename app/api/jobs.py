from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from app.models.job import AlignJobRequest, CreateJobRequest, CreateJobResponse, JobResponse, PublishJobRequest
from app.services.media_analysis import UserInputError
from app.services.publisher import PublishError, SubtitlePublisher

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Nie znaleziono zadania"})


@router.post("", response_model=CreateJobResponse, response_model_by_alias=True, status_code=status.HTTP_202_ACCEPTED)
async def create_job(payload: CreateJobRequest, request: Request) -> CreateJobResponse:
    job = await request.app.state.jobs.create(payload.media_path)
    return CreateJobResponse(jobId=job["id"], status=job["status"])


def response_from_job(job: dict) -> JobResponse:
    return JobResponse(
        jobId=job["id"], status=job["status"], progress=job["progress"], mediaPath=job["media_path"],
        createdAt=job["created_at"], startedAt=job["started_at"], finishedAt=job["finished_at"],
        errorMessage=job["error_message"], resolvedMediaPath=job.get("resolved_media_path"), report=job.get("report"),
    )


@router.get("", response_model=list[JobResponse], response_model_by_alias=True)
async def list_jobs(request: Request, limit: int = 100) -> list[JobResponse]:
    return [response_from_job(job) for job in request.app.state.jobs.list_jobs(limit)]


@router.get("/{job_id}", response_model=JobResponse, response_model_by_alias=True)
async def get_job(job_id: str, request: Request) -> JobResponse:
    job = request.app.state.jobs.get(job_id)
    if not job:
        raise not_found()
    return response_from_job(job)


@router.post("/{job_id}/alignment", status_code=status.HTTP_202_ACCEPTED)
async def align_job(job_id: str, payload: AlignJobRequest, request: Request) -> dict:
    if not request.app.state.jobs.get(job_id):
        raise not_found()
    await request.app.state.jobs.start_alignment(job_id, payload.english_source_id, payload.polish_source_id, payload.mode)
    return {"jobId": job_id, "status": "SELECTING_SOURCES"}


@router.get("/semantic/config")
async def semantic_config(request: Request) -> dict:
    settings = request.app.state.settings
    return {"enabled": settings.openai_semantic_alignment_enabled, "configured": settings.openai_configured,
            "model": settings.openai_model, "limits": {"timeoutSeconds": settings.openai_timeout_seconds,
            "maxRetries": settings.openai_max_retries, "maxRequestsPerJob": settings.openai_max_requests_per_job,
            "maxInputTokensPerJob": settings.openai_max_input_tokens_per_job,
            "maxOutputTokensPerJob": settings.openai_max_output_tokens_per_job,
            "maxConcurrentRequests": settings.openai_max_concurrent_requests}}


@router.get("/publishing/config")
async def publishing_config(request: Request) -> dict:
    return SubtitlePublisher(request.app.state.settings).diagnostic()


@router.get("/{job_id}/publication/preview")
async def publication_preview(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.get(job_id)
    if not job: raise not_found()
    alignment = (job.get("report") or {}).get("alignment") or {}
    try:
        plan = SubtitlePublisher(request.app.state.settings).plan(Path(job.get("resolved_media_path") or job["media_path"]))
        target_name, blocked = plan.target_name, None
    except PublishError as exc:
        target_name, blocked = None, str(exc)
    return {"mode": request.app.state.settings.subtitle_agent_publish_mode,
            "enabled": request.app.state.settings.subtitle_agent_publish_enabled,
            "quality": alignment.get("quality"), "previewSha256": alignment.get("previewSha256"),
            "targetName": target_name, "blockedReason": blocked}


@router.post("/{job_id}/publication")
async def publish_job(job_id: str, payload: PublishJobRequest, request: Request) -> dict:
    if not request.app.state.jobs.get(job_id): raise not_found()
    try:
        return await request.app.state.jobs.publish(job_id, payload.confirmed, payload.expected_preview_sha256)
    except PublishError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.status, "message": str(exc)}) from exc


@router.get("/{job_id}/preview")
async def download_preview(job_id: str, request: Request) -> FileResponse:
    job = request.app.state.jobs.get(job_id)
    if not job:
        raise not_found()
    alignment = (job.get("report") or {}).get("alignment") or {}
    expected = (request.app.state.settings.data_root / "work" / "jobs" / job_id / "preview.AI-Sync.pl.srt").resolve()
    recorded = alignment.get("previewPath")
    if not recorded or Path(recorded).resolve() != expected or not expected.is_file():
        raise HTTPException(status_code=404, detail={"code": "PREVIEW_NOT_FOUND", "message": "Podgląd nie istnieje"})
    return FileResponse(expected, media_type="application/x-subrip", filename="preview.AI-Sync.pl.srt")


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    if not request.app.state.jobs.get(job_id):
        raise not_found()
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    return StreamingResponse(request.app.state.jobs.stream(job_id, after), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
