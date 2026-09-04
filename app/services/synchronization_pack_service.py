from app.services.workpack_pipeline import PipelineRequirements, WorkpackPipelineService


class SynchronizationPackService(WorkpackPipelineService):
    requirements = PipelineRequirements(
        name="PREPARE_SYNC", extract_reference=True, copy_polish=True,
        require_english=True, accept_graphic_reference=True, graphic_reference_requires_ocr=False,
        require_polish=True,
        build_hypotheses=True,
    )
