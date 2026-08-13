from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse

from app.models.job import AlignJobRequest, CreateJobRequest, CreateJobResponse, JobResponse
from app.services.media_analysis import UserInputError

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
    await request.app.state.jobs.start_alignment(job_id, payload.english_source_id, payload.polish_source_id)
    return {"jobId": job_id, "status": "SELECTING_SOURCES"}


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
