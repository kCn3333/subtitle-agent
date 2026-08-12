from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.models.job import CreateJobRequest, CreateJobResponse, JobResponse

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def validate_media_path(value: str, roots: list[Path]) -> str:
    if not value or "\x00" in value:
        raise ValueError("Ścieżka nie może być pusta ani zawierać znaku NUL")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise ValueError("Ścieżka musi być absolutna")
    resolved = candidate.resolve(strict=False)
    allowed = [root.resolve(strict=False) for root in roots]
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise ValueError("Ścieżka musi znajdować się pod jednym z MEDIA_ROOTS")
    return str(resolved)


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Nie znaleziono zadania"})


@router.post("", response_model=CreateJobResponse, response_model_by_alias=True, status_code=status.HTTP_202_ACCEPTED)
async def create_job(payload: CreateJobRequest, request: Request) -> CreateJobResponse:
    try:
        media_path = validate_media_path(payload.media_path, request.app.state.settings.media_roots)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_MEDIA_PATH", "message": str(exc)}) from exc
    job = await request.app.state.jobs.create(media_path)
    return CreateJobResponse(jobId=job["id"], status=job["status"])


@router.get("/{job_id}", response_model=JobResponse, response_model_by_alias=True)
async def get_job(job_id: str, request: Request) -> JobResponse:
    job = request.app.state.jobs.get(job_id)
    if not job:
        raise not_found()
    return JobResponse(jobId=job["id"], status=job["status"], progress=job["progress"],
                       mediaPath=job["media_path"], createdAt=job["created_at"],
                       startedAt=job["started_at"], finishedAt=job["finished_at"],
                       errorMessage=job["error_message"])


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    if not request.app.state.jobs.get(job_id):
        raise not_found()
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    return StreamingResponse(request.app.state.jobs.stream(job_id, after), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
