from app.services.workpack_pipeline import PipelineRequirements, WorkpackPipelineService


class TranslationPackService(WorkpackPipelineService):
    requirements = PipelineRequirements(
        name="PREPARE_TRANSLATION", extract_reference=True, copy_polish=False,
        require_english=True, require_text_english=True, require_polish=False,
        build_hypotheses=False,
    )
