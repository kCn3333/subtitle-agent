from app.services.workpack_pipeline import PipelineRequirements, WorkpackPipelineService


class TranslationPackService(WorkpackPipelineService):
    requirements = PipelineRequirements(
        name="PREPARE_TRANSLATION", extract_reference=True, copy_polish=False,
        require_english=True, accept_graphic_reference=True, graphic_reference_requires_ocr=True,
        require_polish=False,
        build_hypotheses=False,
    )
