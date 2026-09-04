from app.services.workpack_pipeline import PipelineRequirements, WorkpackPipelineService


class InspectionService(WorkpackPipelineService):
    requirements = PipelineRequirements(
        name="INSPECT", extract_reference=True, copy_polish=False,
        require_english=False, accept_graphic_reference=True, graphic_reference_requires_ocr=False,
        require_polish=False,
        build_hypotheses=True,
    )
