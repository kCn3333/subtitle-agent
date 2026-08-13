from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


API_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    VALIDATING_PATH = "VALIDATING_PATH"
    PROBING_MEDIA = "PROBING_MEDIA"
    DISCOVERING_SUBTITLES = "DISCOVERING_SUBTITLES"
    ANALYZING_CANDIDATES = "ANALYZING_CANDIDATES"
    EXTRACTING_REFERENCE = "EXTRACTING_REFERENCE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class CreateJobRequest(BaseModel):
    model_config = API_MODEL_CONFIG
    media_path: str = Field(min_length=1)


class CreateJobResponse(BaseModel):
    model_config = API_MODEL_CONFIG
    job_id: str
    status: JobStatus


class JobResponse(BaseModel):
    model_config = API_MODEL_CONFIG
    job_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    media_path: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None = None
    resolved_media_path: str | None = None
    report: dict | None = None


class JobEvent(BaseModel):
    sequence: int
    timestamp: datetime
    level: str
    stage: JobStatus
    message: str
    progress: int = Field(ge=0, le=100)
