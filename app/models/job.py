from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    INSPECTING = "INSPECTING"
    CHECKING_TOOLS = "CHECKING_TOOLS"
    READY = "READY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    media_path: str = Field(alias="mediaPath", min_length=1)


class CreateJobResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    job_id: str = Field(alias="jobId")
    status: JobStatus


class JobResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    job_id: str = Field(alias="jobId")
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    media_path: str = Field(alias="mediaPath")
    created_at: datetime = Field(alias="createdAt")
    started_at: datetime | None = Field(alias="startedAt")
    finished_at: datetime | None = Field(alias="finishedAt")
    error_message: str | None = Field(alias="errorMessage", default=None)


class JobEvent(BaseModel):
    sequence: int
    timestamp: datetime
    level: str
    stage: JobStatus
    message: str
    progress: int = Field(ge=0, le=100)
