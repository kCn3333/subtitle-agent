from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    SELECTING_REFERENCE = "SELECTING_REFERENCE"
    EXTRACTING_REFERENCE = "EXTRACTING_REFERENCE"
    COLLECTING_POLISH_CANDIDATES = "COLLECTING_POLISH_CANDIDATES"
    BUILDING_TIMELINES = "BUILDING_TIMELINES"
    OCR_RUNNING = "OCR_RUNNING"
    BUILDING_MANIFEST = "BUILDING_MANIFEST"
    BUILDING_WORKPACK = "BUILDING_WORKPACK"
    INSPECTION_READY = "INSPECTION_READY"
    WORKPACK_READY = "WORKPACK_READY"
    WORKPACK_INCOMPLETE = "WORKPACK_INCOMPLETE"
    REFERENCE_AMBIGUOUS = "REFERENCE_AMBIGUOUS"
    NO_ENGLISH_REFERENCE = "NO_ENGLISH_REFERENCE"
    NO_POLISH_CANDIDATES = "NO_POLISH_CANDIDATES"
    SELECTING_SOURCES = "SELECTING_SOURCES"
    PARSING_SUBTITLES = "PARSING_SUBTITLES"
    BUILDING_ANCHORS = "BUILDING_ANCHORS"
    FITTING_MODELS = "FITTING_MODELS"
    SELECTING_STRATEGY = "SELECTING_STRATEGY"
    TRANSFORMING_TIMESTAMPS = "TRANSFORMING_TIMESTAMPS"
    VALIDATING_OUTPUT = "VALIDATING_OUTPUT"
    GENERATING_PREVIEW = "GENERATING_PREVIEW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NEEDS_OCR = "NEEDS_OCR"
    PREPARING_SEMANTIC_WINDOWS = "PREPARING_SEMANTIC_WINDOWS"
    REQUESTING_SEMANTIC_ANCHORS = "REQUESTING_SEMANTIC_ANCHORS"
    VALIDATING_SEMANTIC_ANCHORS = "VALIDATING_SEMANTIC_ANCHORS"
    REFINING_SEMANTIC_ANCHORS = "REFINING_SEMANTIC_ANCHORS"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    AI_BUDGET_EXCEEDED = "AI_BUDGET_EXCEEDED"
    SEMANTIC_FALLBACK = "SEMANTIC_FALLBACK"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    PUBLISH_DISABLED = "PUBLISH_DISABLED"
    PUBLISH_BLOCKED_QUALITY = "PUBLISH_BLOCKED_QUALITY"
    PUBLISH_SOURCE_CHANGED = "PUBLISH_SOURCE_CHANGED"
    PUBLISH_CONFLICT = "PUBLISH_CONFLICT"
    PUBLISH_PERMISSION_DENIED = "PUBLISH_PERMISSION_DENIED"
    PUBLISH_UNSUPPORTED_FILESYSTEM = "PUBLISH_UNSUPPORTED_FILESYSTEM"
    PUBLISH_FAILED = "PUBLISH_FAILED"
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


class AlignmentMode(StrEnum):
    STRUCTURAL_ONLY = "STRUCTURAL_ONLY"
    SEMANTIC_PREFERRED = "SEMANTIC_PREFERRED"
    SEMANTIC_REQUIRED = "SEMANTIC_REQUIRED"


class WorkpackTaskType(StrEnum):
    INSPECT = "INSPECT"
    PREPARE_SYNC = "PREPARE_SYNC"
    PREPARE_TRANSLATION = "PREPARE_TRANSLATION"
    # Legacy task names remain accepted for existing clients and persisted jobs.
    SYNC_ONLY = "SYNC_ONLY"
    LANGUAGE_REVIEW = "LANGUAGE_REVIEW"
    SYNC_AND_LANGUAGE_REVIEW = "SYNC_AND_LANGUAGE_REVIEW"
    TRANSLATE_TO_POLISH = "TRANSLATE_TO_POLISH"
    INSPECT_SUBTITLES = "INSPECT_SUBTITLES"


class WorkpackMode(StrEnum):
    INSPECT = "INSPECT"
    PREPARE_SYNC = "PREPARE_SYNC"
    PREPARE_TRANSLATION = "PREPARE_TRANSLATION"


class CreateTaskRequest(BaseModel):
    model_config = API_MODEL_CONFIG
    media_path: str = Field(min_length=1)
    mode: WorkpackMode


class PrepareWorkpackRequest(BaseModel):
    model_config = API_MODEL_CONFIG
    media_path: str = Field(min_length=1)
    task_type: WorkpackTaskType = WorkpackTaskType.SYNC_AND_LANGUAGE_REVIEW


class RebuildWorkpackRequest(BaseModel):
    model_config = API_MODEL_CONFIG
    reference_source_id: str = Field(min_length=1)


class PublishMode(StrEnum):
    PREVIEW_ONLY = "PREVIEW_ONLY"
    MANUAL = "MANUAL"
    AUTO_HIGH = "AUTO_HIGH"


class AlignJobRequest(BaseModel):
    model_config = API_MODEL_CONFIG
    english_source_id: str | None = None
    polish_source_id: str | None = None
    mode: AlignmentMode = AlignmentMode.SEMANTIC_PREFERRED


class PublishJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, extra="forbid")
    confirmed: bool
    expected_preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    # Keep future/legacy database values readable so one historical row cannot
    # break the entire SSE stream. Known values remain strongly typed.
    stage: JobStatus | str
    message: str
    progress: int = Field(ge=0, le=100)

    @field_validator("stage", mode="before")
    @classmethod
    def preserve_unknown_stage(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return JobStatus(value)
            except ValueError:
                return value
        return value
