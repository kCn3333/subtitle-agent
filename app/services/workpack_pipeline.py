from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.job_manager import JobManager


@dataclass(frozen=True)
class PipelineRequirements:
    name: str
    extract_reference: bool
    copy_polish: bool
    require_english: bool
    accept_graphic_reference: bool
    graphic_reference_requires_ocr: bool
    require_polish: bool
    build_hypotheses: bool


class WorkpackPipelineService:
    requirements: PipelineRequirements

    async def prepare(self, manager: "JobManager", job_id: str, cached: dict | None = None,
                      requested_reference: str | None = None) -> None:
        await manager._build_workpack(job_id, self.requirements, cached, requested_reference)
