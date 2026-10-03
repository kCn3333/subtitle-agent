import asyncio
import json
import random
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Awaitable, Callable, Literal

from openai import (APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI,
                    AuthenticationError, PermissionDeniedError, RateLimitError)
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.services.alignment import Anchor, Cue
from app.services.semantic_prompt import PROMPT_VERSION, SYSTEM_PROMPT


class SemanticMode(StrEnum):
    STRUCTURAL_ONLY = "STRUCTURAL_ONLY"
    SEMANTIC_PREFERRED = "SEMANTIC_PREFERRED"
    SEMANTIC_REQUIRED = "SEMANTIC_REQUIRED"


class SemanticMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    english_cue_ids: list[str] = Field(min_length=1, max_length=4)
    polish_cue_ids: list[str] = Field(min_length=1, max_length=4)
    relation: Literal["ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"]
    confidence: float = Field(ge=0, le=1)
    evidence: Literal["EXACT_MEANING", "PARAPHRASE", "NAME_OR_NUMBER", "SCENE_CONTEXT", "WEAK"]


class SemanticBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str
    matches: list[SemanticMatch]
    insufficient_context: bool


class SemanticError(RuntimeError): pass
class SemanticUnavailable(SemanticError): pass
class SemanticBudgetExceeded(SemanticError): pass
class SemanticBatchError(SemanticError): pass


@dataclass
class SemanticTelemetry:
    model: str
    prompt_version: str = PROMPT_VERSION
    requests: int = 0
    retries: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    accepted_anchors: int = 0
    rejected_anchors: int = 0
    refusal_or_incomplete: int = 0
    elapsed_ms: int = 0
    fallback_used: bool = False
    batches: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict: return asdict(self)


@dataclass
class SemanticResult:
    anchors: list[Anchor]
    telemetry: SemanticTelemetry
    accepted: list[dict]
    rejected: list[dict]


def cue_payload(cue: Cue, duration_ms: int) -> dict:
    return {"cue_id": cue.cue_id, "sequence": cue.sequence, "normalized_text": cue.normalized_text,
            "relative_position": round(cue.start_ms / max(1, duration_ms), 5)}


def build_windows(english: list[Cue], polish: list[Cue], duration_ms: int, size: int, overlap: int) -> list[dict]:
    if not english or not polish: return []
    starts = {0, max(0, len(english) // 2 - size // 2), max(0, len(english) - size)}
    gaps = sorted(range(1, len(english)), key=lambda i: english[i].start_ms - english[i - 1].end_ms, reverse=True)[:3]
    starts.update(max(0, index - size // 2) for index in gaps)
    windows = []
    for number, start in enumerate(sorted(starts)):
        en = english[start:start + size]
        center = sum(c.start_ms for c in en) / max(1, len(en)) / max(1, duration_ms)
        polish_center = round(center * (len(polish) - 1))
        radius = size + overlap
        pl = polish[max(0, polish_center - radius):min(len(polish), polish_center + radius + 1)]
        windows.append({"batch_id": f"coarse-{number}", "pass": 1, "english": en, "polish": pl})
    return windows


def build_refinement_windows(english: list[Cue], polish: list[Cue], accepted: list[dict],
                             size: int, overlap: int) -> list[dict]:
    """Focus pass two around coarse relations, without resending identical batches."""
    en_index = {cue.cue_id: index for index, cue in enumerate(english)}
    pl_index = {cue.cue_id: index for index, cue in enumerate(polish)}
    regions: dict[tuple[int, int], dict] = {}
    for item in accepted:
        en_center = round(sum(en_index[x] for x in item["english_cue_ids"]) / len(item["english_cue_ids"]))
        pl_center = round(sum(pl_index[x] for x in item["polish_cue_ids"]) / len(item["polish_cue_ids"]))
        en_start = max(0, min(len(english) - size, en_center - size // 2))
        pl_radius = size // 2 + overlap
        pl_start = max(0, pl_center - pl_radius)
        key = (en_start, pl_start)
        regions.setdefault(key, {"pass": 2, "english": english[en_start:en_start + size],
                                 "polish": polish[pl_start:min(len(polish), pl_center + pl_radius + 1)]})
    windows = []
    for number, item in enumerate(regions.values()):
        windows.append({**item, "batch_id": f"refine-{number}"})
    return windows


def relation_valid(match: SemanticMatch) -> bool:
    lengths = (len(match.english_cue_ids), len(match.polish_cue_ids))
    expected = {"ONE_TO_ONE": (1, 1), "ONE_TO_MANY": (1, None), "MANY_TO_ONE": (None, 1), "MANY_TO_MANY": (None, None)}
    left, right = expected[match.relation]
    return (left is None or lengths[0] == left) and (right is None or lengths[1] == right) and (
        match.relation != "ONE_TO_MANY" or lengths[1] > 1) and (match.relation != "MANY_TO_ONE" or lengths[0] > 1) and (
        match.relation != "MANY_TO_MANY" or min(lengths) > 1)


def _contiguous(ids: list[str], lookup: dict[str, int]) -> bool:
    try: values = [lookup[item] for item in ids]
    except KeyError: return False
    return values == sorted(values) and values == list(range(values[0], values[0] + len(values)))


def validate_batch(result: SemanticBatchResult, window: dict, min_confidence: float,
                   prior: list[tuple[int, int]] | None = None,
                   global_indexes: tuple[dict[str, int], dict[str, int]] | None = None,
                   expected_source_time: Callable[[int], float] | None = None,
                   max_time_jump_ms: int | None = None) -> tuple[list[dict], list[dict]]:
    en_lookup = {cue.cue_id: index for index, cue in enumerate(window["english"])}
    pl_lookup = {cue.cue_id: index for index, cue in enumerate(window["polish"])}
    en_cues = {cue.cue_id: cue for cue in window["english"]}; pl_cues = {cue.cue_id: cue for cue in window["polish"]}
    used_en, used_pl, accepted, rejected = set(), set(), [], []
    last = prior[-1] if prior else (-1, -1)
    for match in result.matches:
        reason = None
        if match.confidence < min_confidence: reason = "LOW_CONFIDENCE"
        elif not relation_valid(match): reason = "RELATION_CARDINALITY"
        elif not _contiguous(match.english_cue_ids, en_lookup) or not _contiguous(match.polish_cue_ids, pl_lookup): reason = "UNKNOWN_OR_NONCONTIGUOUS_ID"
        elif used_en.intersection(match.english_cue_ids) or used_pl.intersection(match.polish_cue_ids): reason = "CUE_REUSED"
        else:
            en_index = round(sum(en_lookup[x] for x in match.english_cue_ids) / len(match.english_cue_ids))
            pl_index = round(sum(pl_lookup[x] for x in match.polish_cue_ids) / len(match.polish_cue_ids))
            if en_index < last[0] or pl_index < last[1]: reason = "NON_MONOTONIC"
        record = {**match.model_dump(), "batchId": result.batch_id, "validation": "REJECTED" if reason else "ACCEPTED",
                  "reason": reason, "representativeMethod": "median_group_start"}
        if reason: rejected.append(record); continue
        used_en.update(match.english_cue_ids); used_pl.update(match.polish_cue_ids); last = (en_index, pl_index)
        en_time = round(sum(en_cues[x].start_ms for x in match.english_cue_ids) / len(match.english_cue_ids))
        pl_time = round(sum(pl_cues[x].start_ms for x in match.polish_cue_ids) / len(match.polish_cue_ids))
        if expected_source_time and max_time_jump_ms is not None and abs(pl_time - expected_source_time(en_time)) > max_time_jump_ms:
            record.update({"validation": "REJECTED", "reason": "LOCAL_TIME_JUMP"})
            rejected.append(record)
            continue
        if global_indexes:
            en_index = round(sum(global_indexes[0][x] for x in match.english_cue_ids) / len(match.english_cue_ids))
            pl_index = round(sum(global_indexes[1][x] for x in match.polish_cue_ids) / len(match.polish_cue_ids))
        evidence_weight = {"EXACT_MEANING": 1.0, "PARAPHRASE": .92, "NAME_OR_NUMBER": .88, "SCENE_CONTEXT": .78, "WEAK": .55}[match.evidence]
        record.update({"englishIndex": en_index, "polishIndex": pl_index, "referenceTime": en_time,
                       "sourceTime": pl_time, "finalWeight": round(match.confidence * evidence_weight, 4)})
        accepted.append(record)
    return accepted, rejected


class OpenAIAnchorProvider:
    _semaphores: dict[tuple[int, int], asyncio.Semaphore] = {}

    def __init__(self, settings: Settings, client=None, sleep=asyncio.sleep):
        self.settings, self.sleep = settings, sleep
        self.client = client or AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(),
                                            timeout=settings.openai_timeout_seconds, max_retries=0)
        loop_key = (id(asyncio.get_running_loop()), settings.openai_max_concurrent_requests)
        self.semaphore = self._semaphores.setdefault(
            loop_key, asyncio.Semaphore(settings.openai_max_concurrent_requests)
        )

    async def _request(self, window: dict, duration_ms: int, telemetry: SemanticTelemetry) -> SemanticBatchResult:
        payload = {"batch_id": window["batch_id"], "pass": window["pass"],
                   "english": [cue_payload(x, duration_ms) for x in window["english"]],
                   "polish": [cue_payload(x, duration_ms) for x in window["polish"]]}
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        estimated = max(1, len(serialized) // 4)
        if (telemetry.requests >= self.settings.openai_max_requests_per_job
                or telemetry.input_tokens + estimated > self.settings.openai_max_input_tokens_per_job
                or telemetry.output_tokens >= self.settings.openai_max_output_tokens_per_job):
            raise SemanticBudgetExceeded("Przekroczono budżet wejściowy lub liczbę żądań")
        for attempt in range(self.settings.openai_max_retries + 1):
            telemetry.requests += 1
            started = time.monotonic()
            try:
                async with self.semaphore:
                    response = await self.client.responses.parse(
                        model=self.settings.openai_model,
                        reasoning={"effort": self.settings.openai_reasoning_effort},
                        input=[{"role": "system", "content": SYSTEM_PROMPT},
                               {"role": "user", "content": "Untrusted subtitle data JSON follows:\n" + serialized}],
                        text_format=SemanticBatchResult, store=False,
                        max_output_tokens=min(4000, self.settings.openai_max_output_tokens_per_job - telemetry.output_tokens),
                    )
                telemetry.elapsed_ms += round((time.monotonic() - started) * 1000)
                usage = getattr(response, "usage", None)
                input_tokens = int(getattr(usage, "input_tokens", 0) or 0); output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                details = getattr(usage, "input_tokens_details", None)
                telemetry.input_tokens += input_tokens; telemetry.output_tokens += output_tokens
                telemetry.cached_input_tokens += int(getattr(details, "cached_tokens", 0) or 0)
                telemetry.total_tokens += int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
                if telemetry.output_tokens > self.settings.openai_max_output_tokens_per_job: raise SemanticBudgetExceeded("Przekroczono budżet wyjściowy")
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    telemetry.refusal_or_incomplete += 1
                    raise SemanticBatchError("Refusal albo niekompletna odpowiedź")
                if parsed.batch_id != window["batch_id"]: raise SemanticBatchError("Niezgodny identyfikator partii")
                return parsed
            except (AuthenticationError, PermissionDeniedError) as exc:
                raise SemanticUnavailable(type(exc).__name__) from exc
            except APIStatusError as exc:
                if exc.status_code not in {429, 500, 502, 503, 504} or attempt >= self.settings.openai_max_retries:
                    raise SemanticUnavailable(f"HTTP_{exc.status_code}") from exc
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                if attempt >= self.settings.openai_max_retries: raise SemanticUnavailable(type(exc).__name__) from exc
            telemetry.retries += 1
            await self.sleep(min(8, 2 ** attempt) + random.uniform(0, .25))
        raise SemanticUnavailable("Retry exhausted")

    async def provide_async(self, english: list[Cue], polish: list[Cue], duration_ms: int, sources: dict,
                            progress: Callable[[int], Awaitable[None]] | None = None) -> SemanticResult:
        telemetry = SemanticTelemetry(self.settings.openai_model)
        windows = build_windows(english, polish, duration_ms, self.settings.openai_semantic_window_size,
                                self.settings.openai_semantic_window_overlap)
        accepted, rejected = [], []
        global_indexes = ({cue.cue_id: index for index, cue in enumerate(english)},
                          {cue.cue_id: index for index, cue in enumerate(polish)})
        structural: list[Anchor] = sources.get("structural") or []
        def expected(reference_time: int) -> float:
            if not structural:
                return reference_time
            closest = min(structural, key=lambda anchor: abs(anchor.reference_time - reference_time))
            return closest.source_time + reference_time - closest.reference_time
        max_jump = max(15000, round(duration_ms * .08))
        for window in windows:
            result = await self._request(window, duration_ms, telemetry)
            good, bad = validate_batch(result, window, self.settings.openai_min_confidence,
                                       global_indexes=global_indexes,
                                       expected_source_time=expected if structural else None,
                                       max_time_jump_ms=max_jump)
            accepted.extend(good); rejected.extend(bad)
            telemetry.batches.append({"batchId": window["batch_id"], "pass": 1, "accepted": len(good), "rejected": len(bad)})
        # Pass 2 refines bounded areas already localized by pass 1. Repeated relations
        # from overlapping windows become confirmations, not duplicate anchors.
        if progress:
            await progress(2)
        refinements = build_refinement_windows(english, polish, accepted,
                                               self.settings.openai_semantic_window_size,
                                               self.settings.openai_semantic_window_overlap)
        for window in refinements:
            result = await self._request(window, duration_ms, telemetry)
            good, bad = validate_batch(result, window, self.settings.openai_min_confidence,
                                       global_indexes=global_indexes,
                                       expected_source_time=expected if structural else None,
                                       max_time_jump_ms=max_jump)
            accepted.extend(good); rejected.extend(bad)
            telemetry.batches.append({"batchId": window["batch_id"], "pass": 2, "accepted": len(good), "rejected": len(bad)})
        signatures = Counter((tuple(x["english_cue_ids"]), tuple(x["polish_cue_ids"])) for x in accepted)
        deduplicated = {}
        for item in accepted:
            signature = (tuple(item["english_cue_ids"]), tuple(item["polish_cue_ids"]))
            item["confirmationCount"] = signatures[signature]
            item["finalWeight"] = round(min(1.0, item["finalWeight"] + (.05 if signatures[signature] > 1 else 0)), 4)
            deduplicated.setdefault(signature, item)
        # One cue cannot point to two different overlapping-window relations.
        ranked = sorted(deduplicated.values(), key=lambda x: (-x["confirmationCount"], -x["finalWeight"]))
        conflict_free, claimed_en, claimed_pl = [], set(), set()
        for item in ranked:
            if claimed_en.intersection(item["english_cue_ids"]) or claimed_pl.intersection(item["polish_cue_ids"]):
                item["validation"] = "REJECTED"; item["reason"] = "OVERLAPPING_WINDOW_CONFLICT"; rejected.append(item)
                continue
            claimed_en.update(item["english_cue_ids"]); claimed_pl.update(item["polish_cue_ids"]); conflict_free.append(item)
        accepted = sorted(conflict_free, key=lambda x: (x["referenceTime"], x["sourceTime"]))
        monotonic, last = [], (-1, -1)
        for item in accepted:
            pair = (item["referenceTime"], item["sourceTime"])
            if pair[0] < last[0] or pair[1] < last[1]: item["validation"] = "REJECTED"; item["reason"] = "GLOBAL_NON_MONOTONIC"; rejected.append(item)
            else: monotonic.append(item); last = pair
        anchors = [Anchor(x["englishIndex"], x["polishIndex"], x["referenceTime"], x["sourceTime"],
                          x["finalWeight"], "semantic", x["evidence"]) for x in monotonic]
        telemetry.accepted_anchors = len(anchors); telemetry.rejected_anchors = len(rejected)
        if not anchors:
            raise SemanticBatchError("Brak zweryfikowanych kotwic semantycznych")
        return SemanticResult(anchors, telemetry, monotonic, rejected)


class CompositeAnchorProvider:
    @staticmethod
    def combine(structural: list[Anchor], semantic: list[Anchor]) -> list[Anchor]:
        combined = {(anchor.english_index, anchor.polish_index): anchor for anchor in structural}
        for anchor in semantic:
            combined[(anchor.english_index, anchor.polish_index)] = anchor
        return sorted(combined.values(), key=lambda anchor: (anchor.source_time, anchor.reference_time))
