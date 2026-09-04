import base64
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx


class OcrWorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class OcrResult:
    content: bytes
    engine: str
    cue_count: int
    empty_cue_count: int
    first_ms: int | None
    last_start_ms: int | None
    last_ms: int | None
    warnings: list[str]

    def manifest(self) -> dict:
        return {
            "engine": self.engine,
            "cueCount": self.cue_count,
            "emptyCueCount": self.empty_cue_count,
            "firstMs": self.first_ms,
            "lastStartMs": self.last_start_ms,
            "lastMs": self.last_ms,
            "warnings": self.warnings,
        }


def _reference_archive(paths: list[Path]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.writestr(path.name, path.read_bytes())
    return buffer.getvalue()


async def recognize_reference(paths: list[Path], worker_url: str, timeout: float,
                              maximum_output_bytes: int) -> OcrResult:
    if not paths:
        raise OcrWorkerError("Brak plików referencji do OCR")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{worker_url.rstrip('/')}/v1/ocr",
                content=_reference_archive(paths),
                headers={"Content-Type": "application/zip", "X-OCR-Language": "eng"},
            )
    except httpx.HTTPError as exc:
        raise OcrWorkerError(f"Worker OCR jest niedostępny: {type(exc).__name__}") from exc
    if response.status_code != 200:
        detail = response.text.strip().replace("\n", " ")[:300]
        raise OcrWorkerError(f"Worker OCR zwrócił HTTP {response.status_code}: {detail}")
    try:
        payload = response.json()
        content = base64.b64decode(payload["srtBase64"], validate=True)
        cue_count = int(payload["cueCount"])
        empty_count = int(payload.get("emptyCueCount", 0))
        first_ms = int(payload["firstMs"]) if payload.get("firstMs") is not None else None
        last_start_ms = int(payload["lastStartMs"]) if payload.get("lastStartMs") is not None else None
        last_ms = int(payload["lastMs"]) if payload.get("lastMs") is not None else None
    except (KeyError, TypeError, ValueError) as exc:
        raise OcrWorkerError("Worker OCR zwrócił niepoprawną odpowiedź") from exc
    if not content or len(content) > maximum_output_bytes or cue_count < 1:
        raise OcrWorkerError("Worker OCR zwrócił pusty lub zbyt duży wynik")
    return OcrResult(
        content=content, engine=str(payload.get("engine") or "unknown"), cue_count=cue_count,
        empty_cue_count=empty_count, first_ms=first_ms, last_start_ms=last_start_ms, last_ms=last_ms,
        warnings=[str(item)[:300] for item in payload.get("warnings", [])[:20]],
    )
