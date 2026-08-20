from app.services.workpack_pipeline import PipelineRequirements, WorkpackPipelineService


class InspectionService(WorkpackPipelineService):
    requirements = PipelineRequirements(
        name="INSPECT", extract_reference=False, copy_polish=False,
        require_english=False, require_text_english=False, require_polish=False,
        build_hypotheses=False,
    )
