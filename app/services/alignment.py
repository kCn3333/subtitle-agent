import hashlib
import html
import math
import os
import re
import statistics
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from charset_normalizer import from_bytes

TIMING = re.compile(r"(?m)^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3}).*$")
HTML = re.compile(r"<[^>]*>")
ASS = re.compile(r"\{[^}]*\}")
SDH = re.compile(r"(?:^|\s)[\[(][^\])]{1,80}[\])]", re.MULTILINE)
SPEAKER = re.compile(r"(?m)^\s*[-–—]?\s*[A-ZĄĆĘŁŃÓŚŹŻ][A-ZĄĆĘŁŃÓŚŹŻ .'-]{1,30}:\s*")


@dataclass(frozen=True)
class Cue:
    cue_id: str
    sequence: int
    start_ms: int
    end_ms: int
    duration_ms: int
    raw_text: str
    normalized_text: str
    source: str


@dataclass(frozen=True)
class Anchor:
    english_index: int
    polish_index: int
    reference_time: int
    source_time: int
    confidence: float
    origin: str
    reason: str | None = None


class AnchorProvider(Protocol):
    def provide(self, english: list[Cue], polish: list[Cue], duration_ms: int, sources: dict) -> list[Anchor]: ...


def normalize_text(value: str) -> str:
    value = html.unescape(HTML.sub(" ", ASS.sub(" ", value)))
    value = SDH.sub(" ", SPEAKER.sub("", value))
    value = unicodedata.normalize("NFKC", value).translate(str.maketrans({"“": '"', "”": '"', "„": '"', "’": "'", "…": "..."}))
    value = re.sub(r"[^\w\s'\"]+", " ", value.casefold())
    return re.sub(r"\s+", " ", value).strip()


def _ms(parts: tuple[str, ...]) -> int:
    h, m, s, ms = map(int, parts)
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def decode_subtitle(path: Path) -> str:
    raw = path.read_bytes()
    best = from_bytes(raw).best()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else (best.encoding if best else "utf-8")
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        for fallback in ("utf-8-sig", "cp1250", "iso-8859-2", "cp1252"):
            try:
                return raw.decode(fallback)
            except UnicodeDecodeError:
                pass
    return raw.decode("utf-8", errors="replace")


def parse_cues(path: Path, source: str) -> list[Cue]:
    text = decode_subtitle(path).replace("\r\n", "\n").replace("\r", "\n").strip()
    cues: list[Cue] = []
    for position, block in enumerate(re.split(r"\n{2,}", text), 1):
        match = TIMING.search(block)
        if not match:
            continue
        before = block[:match.start()].strip()
        sequence = int(before.splitlines()[-1]) if before.splitlines() and before.splitlines()[-1].isdigit() else position
        raw_text = block[match.end():].lstrip("\n")
        start, end = _ms(match.groups()[:4]), _ms(match.groups()[4:])
        cues.append(Cue(f"{source}:{position}", sequence, start, end, end - start, raw_text, normalize_text(raw_text), source))
    return cues


class FixtureAnchorProvider:
    def __init__(self, anchors: list[Anchor]): self.anchors = anchors
    def provide(self, english: list[Cue], polish: list[Cue], duration_ms: int, sources: dict) -> list[Anchor]:
        return list(self.anchors)


class StructuralAnchorProvider:
    def provide(self, english: list[Cue], polish: list[Cue], duration_ms: int, sources: dict) -> list[Anchor]:
        if len(english) < 6 or len(polish) < 6:
            return []
        count = min(24, len(english), len(polish))
        anchors = []
        for slot in range(count):
            ei = round(slot * (len(english) - 1) / (count - 1))
            pi = round(slot * (len(polish) - 1) / (count - 1))
            e, p = english[ei], polish[pi]
            relative_gap = abs(ei / max(1, len(english) - 1) - pi / max(1, len(polish) - 1))
            duration_ratio = min(e.duration_ms, p.duration_ms) / max(1, max(e.duration_ms, p.duration_ms))
            confidence = max(0.15, min(0.72, 0.55 + duration_ratio * 0.17 - relative_gap))
            anchors.append(Anchor(ei, pi, e.start_ms, p.start_ms, confidence, "structural", "kolejność, położenie i czas wyświetlania"))
        return anchors


def percentile(values: list[float], fraction: float) -> float:
    if not values: return math.inf
    ordered = sorted(values); index = math.ceil(fraction * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def _metrics(name: str, anchors: list[Anchor], predict, parameters: dict, complexity: float) -> dict:
    residuals = [abs(a.reference_time - predict(a.source_time)) for a in anchors]
    median = statistics.median(residuals) if residuals else math.inf
    threshold = max(250.0, min(1500.0, median * 2.5))
    inliers = [r for r in residuals if r <= threshold]
    positions = [a.reference_time for a, r in zip(anchors, residuals) if r <= threshold]
    coverage = (max(positions) - min(positions)) / max(1, max(a.reference_time for a in anchors)) if len(positions) > 1 else 0
    score = (len(inliers) / max(1, len(anchors))) * 100 + coverage * 25 - statistics.median(inliers or residuals or [99999]) / 100 - complexity
    return {"strategy": name, **parameters, "pointCount": len(anchors), "inlierCount": len(inliers),
            "inlierRatio": len(inliers) / max(1, len(anchors)), "medianResidualMs": round(statistics.median(inliers or residuals or [math.inf])),
            "p95ResidualMs": round(percentile(inliers or residuals, .95)), "maxResidualMs": round(max(inliers or residuals or [math.inf])),
            "coverage": round(coverage, 4), "complexityPenalty": complexity, "score": round(score, 4), "predict": predict}


def _robust_affine(anchors: list[Anchor], min_scale: float, max_scale: float) -> tuple[float, float]:
    slopes = []
    for i, left in enumerate(anchors):
        for right in anchors[i + 1:]:
            delta = right.source_time - left.source_time
            if abs(delta) >= 1000:
                slope = (right.reference_time - left.reference_time) / delta
                if min_scale <= slope <= max_scale: slopes.append(slope)
    scale = statistics.median(slopes) if slopes else 1.0
    offset = statistics.median([a.reference_time - scale * a.source_time for a in anchors]) if anchors else 0
    return scale, offset


def fit_models(anchors: list[Anchor], min_scale: float = .94, max_scale: float = 1.06,
               max_segments: int = 3, min_points: int = 4) -> list[dict]:
    if not anchors: return []
    offset = statistics.median([a.reference_time - a.source_time for a in anchors])
    models = [_metrics("IDENTITY", anchors, lambda value: value, {"offsetMs": 0, "scale": 1.0, "segments": []}, 0),
              _metrics("GLOBAL_OFFSET", anchors, lambda value, o=offset: value + o, {"offsetMs": round(offset), "scale": 1.0, "segments": []}, 2)]
    scale, affine_offset = _robust_affine(anchors, min_scale, max_scale)
    models.append(_metrics("AFFINE_DRIFT", anchors, lambda value, s=scale, o=affine_offset: s * value + o,
                           {"offsetMs": round(affine_offset), "scale": round(scale, 8), "segments": []}, 5))
    ordered = sorted(anchors, key=lambda a: a.source_time)
    best_piece = None
    for parts in range(2, min(max_segments, len(ordered) // min_points) + 1):
        groups = [ordered[round(i * len(ordered) / parts):round((i + 1) * len(ordered) / parts)] for i in range(parts)]
        if any(len(group) < min_points for group in groups): continue
        segments = []
        for group in groups:
            s, o = _robust_affine(group, min_scale, max_scale)
            segments.append({"sourceStartMs": group[0].source_time, "sourceEndMs": group[-1].source_time, "scale": s, "offsetMs": o})
        if any(segments[i]["scale"] <= 0 for i in range(len(segments))): continue
        def piece(value, ss=segments):
            segment = next((item for item in ss if value <= item["sourceEndMs"]), ss[-1])
            return segment["scale"] * value + segment["offsetMs"]
        model = _metrics("PIECEWISE_LINEAR", anchors, piece, {"offsetMs": None, "scale": None,
                         "segments": [{**x, "scale": round(x["scale"], 8), "offsetMs": round(x["offsetMs"])} for x in segments]}, 10 * (parts - 1))
        if best_piece is None or model["score"] > best_piece["score"]: best_piece = model
    if best_piece: models.append(best_piece)
    return models


def select_model(models: list[dict]) -> dict | None:
    if not models: return None
    best = max(models, key=lambda model: (model["score"], -model["complexityPenalty"]))
    simpler = [model for model in models if model["complexityPenalty"] < best["complexityPenalty"] and best["score"] - model["score"] < 2]
    return max(simpler, key=lambda model: model["score"], default=best)


def quality(model: dict | None) -> str:
    if not model or model["pointCount"] < 4 or model["coverage"] < .25: return "UNUSABLE"
    if model["inlierCount"] >= 10 and model["coverage"] >= .7 and model["p95ResidualMs"] <= 750: return "HIGH"
    if model["inlierCount"] >= 6 and model["coverage"] >= .5 and model["p95ResidualMs"] <= 1500: return "MEDIUM"
    return "LOW"


def transform(cues: list[Cue], model: dict, duration_ms: int, tolerance_ms: int = 1000) -> tuple[list[Cue], dict]:
    predict = model["predict"]; output = []; negative = reversed_count = overlaps = 0
    for cue in cues:
        start, end = round(predict(cue.start_ms)), round(predict(cue.end_ms))
        negative += start < 0
        if end <= start: reversed_count += 1
        output.append(Cue(cue.cue_id, cue.sequence, max(0, start), min(duration_ms + tolerance_ms, end),
                          end - start, cue.raw_text, cue.normalized_text, cue.source))
    overlaps = sum(output[i].start_ms < output[i - 1].end_ms for i in range(1, len(output)))
    return output, {"negativeTimesBeforeClamp": negative, "reversedSegments": reversed_count, "overlappingSegments": overlaps}


def _stamp(value: int) -> str:
    value = max(0, value); h, rest = divmod(value, 3600000); m, rest = divmod(rest, 60000); s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_preview(cues: list[Cue], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n\n".join(f"{cue.sequence}\n{_stamp(cue.start_ms)} --> {_stamp(cue.end_ms)}\n{cue.raw_text}" for cue in cues) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def public_model(model: dict) -> dict:
    return {key: value for key, value in model.items() if key != "predict"}
