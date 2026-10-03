"""One text-reference contract shared by pack preparation and synchronization."""
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.alignment import sha256
from app.services.media_analysis import UserInputError, reference_source_id


@dataclass(frozen=True)
class PreparedReference:
    sourceId: str
    originalKind: str
    textPath: str
    sha256: str
    provenance: str
    ocrQuality: dict | None

    @classmethod
    def create(cls, source: dict, path: Path, ocr_quality: dict | None = None):
        return cls(reference_source_id(source), source.get('type', 'text'), str(path), sha256(path),
                   'ocr' if source.get('type') == 'graphic' else 'extraction', ocr_quality)

    def to_dict(self): return asdict(self)

    @classmethod
    def verified(cls, record: dict, source: dict):
        result = cls(**record)
        if result.sourceId != reference_source_id(source):
            raise UserInputError('Wybrana referencja wymaga ponownego przygotowania')
        path = Path(result.textPath)
        if not path.is_file() or sha256(path) != result.sha256:
            raise UserInputError('Plik przygotowanej referencji zmienił się lub wygasł')
        if result.originalKind == 'graphic' and (result.ocrQuality or {}).get('structuralQuality') != 'GOOD':
            raise UserInputError('Wadliwa struktura OCR blokuje synchronizację')
        return result
