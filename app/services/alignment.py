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


def parse_cues(path: Path, source: str, strict: bool = False) -> list[Cue]:
    text = decode_subtitle(path).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    cues: list[Cue] = []
    for position, block in enumerate(re.split(r"\n{2,}", text), 1):
        match = TIMING.search(block)
        if not match:
            if strict and block.strip(): raise ValueError("Unparseable subtitle block")
            continue
        before = block[:match.start()].strip()
        sequence = int(before.splitlines()[-1]) if before.splitlines() and before.splitlines()[-1].isdigit() else position
        raw_text = block[match.end():].lstrip("\n")
        if strict and any(int(match.groups()[i]) > 59 for i in (1,2,5,6)):
            raise ValueError("Invalid SRT timestamp")
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


def _metrics(name: str, anchors: list[Anchor], predict, parameters: dict, complexity: float, duration_ms: int | None = None) -> dict:
    residuals = [abs(a.reference_time - predict(a.source_time)) for a in anchors]
    median = weighted_median([(r,a.confidence) for r,a in zip(residuals,anchors)]) if residuals else math.inf
    threshold = max(250.0, min(1500.0, median * 2.5))
    inliers = [r for r in residuals if r <= threshold]
    positions = [a.reference_time for a, r in zip(anchors, residuals) if r <= threshold and a.confidence >= max(x.confidence for x in anchors)*.1]
    coverage = (max(positions) - min(positions)) / max(1, duration_ms or max(a.reference_time for a in anchors)) if len(positions) > 1 else 0
    score = (sum(a.confidence for a, r in zip(anchors, residuals) if r <= threshold) / max(1e-9, sum(a.confidence for a in anchors))) * 100 + coverage * 25 - statistics.median(inliers or residuals or [99999]) / 100 - complexity
    return {"strategy": name, **parameters, "pointCount": len(anchors), "inlierCount": len(inliers),
            "inlierRatio": len(inliers) / max(1, len(anchors)), "medianResidualMs": round(statistics.median(inliers or residuals or [math.inf])),
            "p95ResidualMs": round(percentile(inliers or residuals, .95)), "maxResidualMs": round(max(inliers or residuals or [math.inf])),
            "coverage": round(coverage, 4), "complexityPenalty": complexity, "score": round(score, 4), "predict": predict}


def weighted_median(values: list[tuple[float, float]]) -> float:
    ordered = sorted((value, max(0.0, weight)) for value, weight in values)
    total = sum(weight for _, weight in ordered)
    if not ordered: return 0.0
    if total == 0: return statistics.median(value for value, _ in ordered)
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= total / 2: return value
    return ordered[-1][0]


def _robust_affine(anchors: list[Anchor], min_scale: float, max_scale: float) -> tuple[float, float]:
    # Bounded weighted Theil-Sen; do not construct quadratic storage for a movie.
    sampled = anchors[::max(1, math.ceil(len(anchors) / 128))]
    slopes = []
    for i, left in enumerate(sampled):
        for right in sampled[i + 1:]:
            delta = right.source_time - left.source_time
            if abs(delta) >= 1000:
                slope = (right.reference_time - left.reference_time) / delta
                if min_scale <= slope <= max_scale:
                    slopes.append((slope, left.confidence * right.confidence))
    scale = weighted_median(slopes) if slopes else 1.0
    offset = weighted_median([(a.reference_time - scale * a.source_time, a.confidence) for a in anchors])
    return scale, offset


def fit_models(anchors: list[Anchor], min_scale: float = .94, max_scale: float = 1.06,
               max_segments: int = 3, min_points: int = 4, duration_ms: int | None = None) -> list[dict]:
    if not anchors: return []
    # Structural hypotheses must never outweigh confirmed content relations.
    content = [a for a in anchors if a.origin != "structural"]
    anchors = content or anchors
    offset = weighted_median([(a.reference_time - a.source_time, a.confidence) for a in anchors])
    def metrics(name, predict, params, cost):
        return _metrics(name, anchors, predict, params, cost, duration_ms)
    models = [metrics("IDENTITY", lambda value: value, {"offsetMs": 0, "scale": 1.0, "segments": []}, 0),
              metrics("GLOBAL_OFFSET", lambda value: value + offset, {"offsetMs": round(offset), "scale": 1.0, "segments": []}, 2)]
    scale, affine_offset = _robust_affine(anchors, min_scale, max_scale)
    models.append(metrics("AFFINE_DRIFT", lambda value: scale * value + affine_offset,
                          {"offsetMs": round(affine_offset), "scale": round(scale, 8), "segments": []}, 5))
    ordered = sorted(anchors, key=lambda a: a.source_time)
    # Changes are supported by sustained residual steps, not equal list partitions.
    residual = [a.reference_time - (scale * a.source_time + affine_offset) for a in ordered]
    cuts = []
    for i in range(min_points, len(ordered) - min_points + 1):
        left = statistics.median(residual[max(0, i-min_points):i])
        right = statistics.median(residual[i:i+min_points])
        if abs(right-left) > 1500:
            cuts.append((abs(right-left), i))
    boundaries = []
    for _, i in sorted(cuts, reverse=True):
        if all(abs(i-j) >= min_points for j in boundaries): boundaries.append(i)
        if len(boundaries) >= max_segments-1: break
    if boundaries and max_segments > 1:
        indexes = [0, *sorted(boundaries), len(ordered)]
        groups = [ordered[a:b] for a,b in zip(indexes, indexes[1:])]
        if all(len(group) >= min_points for group in groups):
            segments = []
            for group in groups:
                s, o = _robust_affine(group, min_scale, max_scale)
                segments.append({"sourceStartMs": group[0].source_time, "sourceEndMs": group[-1].source_time,
                                 "scale": s, "offsetMs": o})
            def piece(value, ss=segments):
                # No interpolation across unmatched scene gaps. Preserve those times
                # and flag them below for review.
                if value < ss[0]["sourceStartMs"]: item = ss[0]
                elif value > ss[-1]["sourceEndMs"]: item = ss[-1]
                else:
                    item = next((x for x in ss if x["sourceStartMs"] <= value <= x["sourceEndMs"]), None)
                return item["scale"] * value + item["offsetMs"] if item else value
            models.append(metrics("PIECEWISE_LINEAR", piece, {"offsetMs": None, "scale": None,
                "segments": segments, "uncertainRanges": [{"startMs": a["sourceEndMs"], "endMs": b["sourceStartMs"]}
                    for a,b in zip(segments, segments[1:])]}, 10 * (len(groups)-1)))
    return models


def select_model(models: list[dict]) -> dict | None:
    if not models: return None
    best = max(models, key=lambda model: (model["score"], -model["complexityPenalty"]))
    simpler = [model for model in models if model["complexityPenalty"] < best["complexityPenalty"] and best["score"] - model["score"] < 2]
    return max(simpler, key=lambda model: model["score"], default=best)


def quality(model: dict | None) -> str:
    if not model or model["pointCount"] < 4 or model["coverage"] < .25: return "UNUSABLE"
    if model.get("independentValidated") and model["inlierCount"] >= 10 and model["coverage"] >= .7 and model["p95ResidualMs"] <= 750: return "HIGH"
    if model.get("independentValidated") and model["inlierCount"] >= 6 and model["coverage"] >= .5 and model["p95ResidualMs"] <= 1500: return "MEDIUM"
    return "LOW"


def transform(cues: list[Cue], model: dict, duration_ms: int, tolerance_ms: int = 1000) -> tuple[list[Cue], dict]:
    predict = model["predict"]; output = []; negative = reversed_count = clamped = 0
    for cue in cues:
        start, end = round(predict(cue.start_ms)), round(predict(cue.end_ms))
        negative += start < 0
        final_start, final_end = max(0, start), max(0, min(duration_ms + tolerance_ms, end))
        clamped += (final_start, final_end) != (start, end)
        reversed_count += final_end <= final_start
        output.append(Cue(cue.cue_id, cue.sequence, final_start, final_end,
                          final_end - final_start, cue.raw_text, cue.normalized_text, cue.source))
    original = {i for i in range(1, len(cues)) if cues[i].start_ms < cues[i-1].end_ms}
    overlaps = {i for i in range(1, len(output)) if output[i].start_ms < output[i-1].end_ms}
    return output, {"negativeTimesBeforeClamp": negative, "clampedSegments": clamped,
        "reversedSegments": reversed_count, "overlappingSegments": len(overlaps),
        "existingOverlaps": len(original), "introducedOverlaps": len(overlaps-original),
        "outOfRangeSegments": sum(c.start_ms < 0 or c.end_ms > duration_ms+tolerance_ms for c in output),
        "orderErrors": sum(output[i].start_ms < output[i-1].start_ms for i in range(1,len(output))),
        "segmentCountPreserved": len(cues) == len(output),
        "textPreserved": [c.raw_text for c in cues] == [c.raw_text for c in output]}


def coverage_report(anchors: list[Anchor], english: list[Cue], duration_ms: int) -> dict:
    if not english: return {"dialogueCoverage": 0, "materialCoverage": 0, "largestGapMs": duration_ms}
    first, last = english[0].start_ms, english[-1].end_ms
    span = max(1, last-first)
    positions = sorted(a.reference_time for a in anchors)
    occupied = {min(9, max(0, int((p-first)*10/span))) for p in positions}
    dialogue_bins = {min(9, max(0, int((c.start_ms-first)*10/span))) for c in english}
    edges = [first, *positions, last]
    return {"materialCoverage": (positions[-1]-positions[0])/max(1,duration_ms) if len(positions)>1 else 0,
        "dialogueCoverage": len(occupied & dialogue_bins)/max(1,len(dialogue_bins)),
        "occupiedBins": sorted(occupied), "dialogueBins": sorted(dialogue_bins),
        "largestGapMs": max((b-a for a,b in zip(edges,edges[1:])), default=span),
        "beginning": 0 in occupied, "middle": bool(occupied & {4,5}), "end": 9 in occupied}


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
