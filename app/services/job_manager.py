import asyncio
import json
import sqlite3
import threading
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from app.core.config import Settings
from app.models.job import AlignmentMode, JobEvent, JobStatus, WorkpackTaskType
from app.services.media_analysis import (
    UserInputError, discover_external_subtitles, discover_external_subtitles_with_rejections, extract_reference,
    parse_media_identity, probe_media, rank_english, rank_polish, validate_media_path,
)
from app.services.inspection_service import InspectionService
from app.services.synchronization_pack_service import SynchronizationPackService
from app.services.translation_pack_service import TranslationPackService
from app.services.workpack_pipeline import PipelineRequirements, WorkpackPipelineService
from app.services.process_runner import ProcessExecutionError, ProcessTimeoutError
from app.services.alignment import (StructuralAnchorProvider, fit_models, parse_cues, public_model,
                                    quality, select_model, sha256, transform, write_preview)
from app.services.semantic import (CompositeAnchorProvider, OpenAIAnchorProvider, SemanticBatchError,
                                   SemanticBudgetExceeded, SemanticUnavailable)
from app.services.publisher import (PublishBlockedQuality, PublishConflict, PublishDisabled, PublishError,
                                    PublishSourceChanged, SubtitlePublisher, identity)
from app.services.workpack import (SCHEMA_VERSION, build_zip, copy_polish_candidates, diagnostic_hypotheses,
                                   extract_embedded, graphic_timeline, media_summary, request_text, safe_filename,
                                   sha256_file, subtitle_streams, timeline, write_json)


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.INTERRUPTED, JobStatus.REVIEW_REQUIRED,
            JobStatus.NEEDS_OCR, JobStatus.AI_UNAVAILABLE, JobStatus.AI_BUDGET_EXCEEDED,
            JobStatus.READY_TO_PUBLISH, JobStatus.PUBLISHED, JobStatus.PUBLISH_DISABLED,
            JobStatus.PUBLISH_BLOCKED_QUALITY, JobStatus.PUBLISH_SOURCE_CHANGED, JobStatus.PUBLISH_CONFLICT,
            JobStatus.PUBLISH_PERMISSION_DENIED, JobStatus.PUBLISH_UNSUPPORTED_FILESYSTEM, JobStatus.PUBLISH_FAILED}
TERMINAL.update({JobStatus.WORKPACK_READY, JobStatus.WORKPACK_INCOMPLETE})
LEGACY_EVENT_STAGES = {
    "INSPECTING": JobStatus.VALIDATING_PATH,
    "CHECKING_TOOLS": JobStatus.PROBING_MEDIA,
    "READY": JobStatus.COMPLETED,
}


def now() -> datetime:
    return datetime.now().astimezone()


def candidate_label(candidate: dict | None) -> str:
    if not candidate:
        return "brak"
    if candidate.get("name"):
        return str(candidate["name"])
    return f"strumień {candidate.get('streamIndex', '?')} ({candidate.get('codec', 'nieznany kodek')})"


def workpack_service(task_type: WorkpackTaskType) -> WorkpackPipelineService:
    if task_type in {WorkpackTaskType.INSPECT, WorkpackTaskType.INSPECT_SUBTITLES}:
        return InspectionService()
    if task_type in {WorkpackTaskType.PREPARE_TRANSLATION, WorkpackTaskType.TRANSLATE_TO_POLISH}:
        return TranslationPackService()
    return SynchronizationPackService()


class JobManager:
    def __init__(self, db_path: Path, settings: Settings):
        self.db_path = db_path
        self.settings = settings
        self.max_concurrent = settings.max_concurrent_jobs
        self._lock = threading.RLock()
        self._conditions: dict[str, asyncio.Condition] = {}
        self._publish_locks: dict[str, asyncio.Lock] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._closed = False
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self._lock, self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, media_path TEXT NOT NULL, status TEXT NOT NULL,
                    progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error_message TEXT,
                    resolved_media_path TEXT, report_json TEXT, job_type TEXT NOT NULL DEFAULT 'ANALYZE_MEDIA',
                    task_type TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL, sequence INTEGER NOT NULL, timestamp TEXT NOT NULL,
                    level TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL,
                    progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                    PRIMARY KEY(job_id, sequence), FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE TABLE IF NOT EXISTS publication_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, attempted_at TEXT NOT NULL,
                    mode TEXT NOT NULL, result TEXT NOT NULL, quality TEXT, source_ro_path TEXT,
                    target_rw_path TEXT, target_name TEXT, version INTEGER, preview_sha256 TEXT,
                    published_sha256 TEXT, size_bytes INTEGER, source_identity_json TEXT,
                    automatic INTEGER NOT NULL, error_message TEXT,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "resolved_media_path" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN resolved_media_path TEXT")
            if "report_json" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN report_json TEXT")
            if "job_type" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'ANALYZE_MEDIA'")
            if "task_type" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN task_type TEXT")
            for legacy, current in LEGACY_EVENT_STAGES.items():
                db.execute("UPDATE events SET stage=? WHERE stage=?", (current, legacy))
            stamp = now().isoformat()
            terminal_values = tuple(str(item) for item in TERMINAL)
            placeholders = ",".join("?" for _ in terminal_values)
            db.execute(f"UPDATE jobs SET status=?, finished_at=?, error_message=? WHERE status NOT IN ({placeholders})",
                       (JobStatus.INTERRUPTED, stamp, "Zadanie przerwane przez restart aplikacji", *terminal_values))

    async def start(self) -> None:
        self._workers = [asyncio.create_task(self._worker()) for _ in range(self.max_concurrent)]

    async def close(self) -> None:
        self._closed = True
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with suppress(asyncio.CancelledError):
                await worker

    async def create(self, media_path: str) -> dict:
        job_id = str(uuid.uuid4())
        stamp = now().isoformat()
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO jobs
                (id,media_path,status,progress,created_at,started_at,finished_at,error_message,resolved_media_path,report_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (job_id, media_path, JobStatus.QUEUED, 0, stamp, None, None, None, None, None))
            self._insert_event(db, job_id, "INFO", JobStatus.QUEUED, "Zadanie zostało utworzone", 0)
        self._conditions[job_id] = asyncio.Condition()
        await self._queue.put(job_id)
        return self.get(job_id)

    async def create_workpack(self, media_path: str, task_type: WorkpackTaskType) -> dict:
        job_id = str(uuid.uuid4()); stamp = now().isoformat()
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO jobs
                (id,media_path,status,progress,created_at,started_at,finished_at,error_message,resolved_media_path,
                 report_json,job_type,task_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, media_path, JobStatus.QUEUED, 0, stamp, None, None, None, None, None,
                 "PREPARE_WORKPACK", task_type.value))
            self._insert_event(db, job_id, "INFO", JobStatus.QUEUED, "Zadanie przygotowania workpacka zostało utworzone", 0)
        self._conditions[job_id] = asyncio.Condition(); await self._queue.put(job_id)
        return self.get(job_id)

    async def rebuild_workpack(self, job_id: str, reference_source_id: str) -> None:
        job = self.get(job_id)
        if not job or job.get("job_type") != "PREPARE_WORKPACK" or not job.get("report"):
            raise UserInputError("Nie znaleziono danych workpacka")
        detected = job["report"].get("englishRanking") or []
        if reference_source_id not in {f"{item.get('sourceType')}:{item.get('streamIndex')}" for item in detected}:
            raise UserInputError("Wybrana referencja nie została wykryta w analizie")
        async def run() -> None:
            try:
                task_type = WorkpackTaskType(job.get("task_type") or WorkpackTaskType.SYNC_AND_LANGUAGE_REVIEW)
                await workpack_service(task_type).prepare(self, job_id, job["report"], reference_source_id)
            except Exception as exc:
                message = f"Ponowne budowanie workpacka nie powiodło się ({type(exc).__name__})"
                with self._lock, self._connect() as db: db.execute("UPDATE jobs SET error_message=? WHERE id=?", (message, job_id))
                await self._emit(job_id, "ERROR", JobStatus.FAILED, message, 100)
        asyncio.create_task(run())

    def get(self, job_id: str) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["report"] = json.loads(result.pop("report_json")) if result.get("report_json") else None
        return result

    def list_jobs(self, limit: int = 100) -> list[dict]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 500),)).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["report"] = json.loads(item.pop("report_json")) if item.get("report_json") else None
            results.append(item)
        return results

    def events(self, job_id: str) -> list[JobEvent]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE job_id=? ORDER BY sequence", (job_id,)).fetchall()
        return [JobEvent(**dict(row)) for row in rows]

    def _insert_event(self, db: sqlite3.Connection, job_id: str, level: str, stage: JobStatus,
                      message: str, progress: int) -> None:
        progress = max(0, min(100, progress))
        seq = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE job_id=?", (job_id,)).fetchone()[0]
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                   (job_id, seq, now().isoformat(), level, stage, message, progress))
        db.execute("DELETE FROM events WHERE job_id=? AND sequence <= ?",
                   (job_id, max(0, seq - 500)))

    async def _emit(self, job_id: str, level: str, stage: JobStatus, message: str, progress: int) -> None:
        stamp = now().isoformat()
        started = stamp if stage == JobStatus.VALIDATING_PATH else None
        finished = stamp if stage in TERMINAL else None
        with self._lock, self._connect() as db:
            self._insert_event(db, job_id, level, stage, message, progress)
            db.execute("UPDATE jobs SET status=?, progress=?, started_at=COALESCE(started_at,?), finished_at=COALESCE(?,finished_at) WHERE id=?",
                       (stage, progress, started, finished, job_id))
        condition = self._conditions.setdefault(job_id, asyncio.Condition())
        async with condition:
            condition.notify_all()

    def _save_report(self, job_id: str, report: dict, resolved_path: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE jobs SET report_json=?, resolved_media_path=? WHERE id=?",
                       (json.dumps(report, ensure_ascii=False), resolved_path, job_id))

    def _audit_publication(self, job_id: str, mode: str, result: str, quality: str | None,
                           automatic: bool, details: dict | None = None, error: str | None = None) -> None:
        details = details or {}
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO publication_attempts
                (job_id,attempted_at,mode,result,quality,source_ro_path,target_rw_path,target_name,version,
                 preview_sha256,published_sha256,size_bytes,source_identity_json,automatic,error_message)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                job_id, details.get("attemptedAt", now().isoformat()), mode, result, quality,
                details.get("sourcePath"), details.get("targetPath"), details.get("targetName"),
                details.get("version"), details.get("previewSha256"), details.get("publishedSha256"),
                details.get("sizeBytes"), json.dumps(details.get("mediaIdentity")) if details.get("mediaIdentity") else None,
                int(automatic), error))

    def publication_attempts(self, job_id: str) -> list[dict]:
        with self._lock, self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM publication_attempts WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]

    async def publish(self, job_id: str, confirmed: bool, expected_hash: str, automatic: bool = False) -> dict:
        lock = self._publish_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            return await self._publish_once(job_id, confirmed, expected_hash, automatic)

    async def _publish_once(self, job_id: str, confirmed: bool, expected_hash: str,
                            automatic: bool = False) -> dict:
        job = self.get(job_id)
        if not job or not job.get("report"):
            raise UserInputError("Nie znaleziono raportu zadania")
        report = job["report"]; alignment = report.get("alignment") or {}; existing = report.get("publication") or {}
        publisher = SubtitlePublisher(self.settings); mode = self.settings.subtitle_agent_publish_mode
        async def reject(exc: PublishError) -> None:
            details = {"attemptedAt": now().isoformat(), "sourcePath": job.get("resolved_media_path") or job["media_path"],
                       "previewSha256": alignment.get("previewSha256")}
            report["publication"] = {"status": exc.status, "message": str(exc), **details}
            self._save_report(job_id, report, job.get("resolved_media_path") or job["media_path"])
            self._audit_publication(job_id, mode, exc.status, alignment.get("quality"), automatic, details, str(exc))
            await self._emit(job_id, "WARNING", JobStatus(exc.status), str(exc), 100)
            raise exc
        if existing.get("status") == "PUBLISHED":
            target = Path(existing["targetPath"])
            if target.is_file() and sha256(target) == existing.get("publishedSha256"):
                return existing
            exc = PublishConflict("Baza wskazuje publikację, ale plik zniknął albo zmienił zawartość")
            existing.update({"status": exc.status, "message": str(exc)})
            report["publication"] = existing; self._save_report(job_id, report, job.get("resolved_media_path") or job["media_path"])
            self._audit_publication(job_id, mode, exc.status, alignment.get("quality"), automatic,
                                    {"targetPath": str(target)}, str(exc))
            await self._emit(job_id, "WARNING", JobStatus.PUBLISH_CONFLICT, str(exc), 100)
            raise exc
        if not self.settings.subtitle_agent_publish_enabled or mode == "PREVIEW_ONLY":
            await reject(PublishDisabled("Publikowanie jest wyłączone lub działa w PREVIEW_ONLY"))
        if not automatic and (mode != "MANUAL" or not confirmed):
            await reject(PublishDisabled("Ręczna publikacja wymaga trybu MANUAL i potwierdzenia"))
        quality_grade = alignment.get("quality")
        if quality_grade not in ({"HIGH"} if automatic else {"HIGH", "MEDIUM"}):
            await reject(PublishBlockedQuality("Jakość synchronizacji nie pozwala na publikację"))
        if alignment.get("warnings") or alignment.get("status") != "COMPLETED":
            await reject(PublishBlockedQuality("Wynik ma krytyczne ostrzeżenia albo nie przeszedł walidacji"))
        semantic = report.get("semanticAlignment") or {}; usage = semantic.get("usage") or {}
        if automatic and self.settings.subtitle_agent_auto_publish_require_semantic and not usage.get("accepted_anchors"):
            await reject(PublishBlockedQuality("AUTO_HIGH wymaga zaakceptowanych kotwic semantycznych"))
        if automatic and semantic.get("fallbackUsed"):
            await reject(PublishBlockedQuality("AUTO_HIGH blokuje wynik z fallbackiem"))
        preview_hash = alignment.get("previewSha256"); preview = Path(alignment.get("previewPath") or "")
        if expected_hash != preview_hash:
            await reject(PublishSourceChanged("Oczekiwany SHA-256 preview jest nieaktualny"))
        media = Path(job.get("resolved_media_path") or job["media_path"])
        polish = Path((alignment.get("selectedPolish") or {}).get("path") or "")
        if alignment.get("mediaIdentity") != identity(media):
            await reject(PublishSourceChanged("Film zmienił się od synchronizacji"))
        if not polish.is_file() or alignment.get("inputSha256") != sha256(polish):
            await reject(PublishSourceChanged("Źródłowy polski SRT zmienił się od synchronizacji"))
        await self._emit(job_id, "INFO", JobStatus.PUBLISHING, "Bezpieczna publikacja nowej wersji napisów", 98)
        try:
            plan = await asyncio.to_thread(publisher.plan, media)
            result = await asyncio.to_thread(publisher.publish, plan, preview, preview_hash)
            result.update({"status": "PUBLISHED", "mode": mode, "quality": quality_grade,
                           "automatic": automatic, "sourcePath": str(media),
                           "message": "Utworzono nowy plik napisów. Żaden istniejący plik nie został zmieniony ani usunięty."})
            report["publication"] = result; self._save_report(job_id, report, str(media))
            self._audit_publication(job_id, mode, "PUBLISHED", quality_grade, automatic, result)
            await self._emit(job_id, "SUCCESS", JobStatus.PUBLISHED, result["message"], 100)
            return result
        except PublishError as exc:
            details = {"sourcePath": str(media), "previewSha256": preview_hash}
            report["publication"] = {"status": exc.status, "message": str(exc), **details}
            self._save_report(job_id, report, str(media)); self._audit_publication(
                job_id, mode, exc.status, quality_grade, automatic, details, str(exc))
            await self._emit(job_id, "WARNING", JobStatus(exc.status), str(exc), 100)
            raise

    async def start_alignment(self, job_id: str, english_source_id: str | None, polish_source_id: str | None,
                              mode: AlignmentMode) -> None:
        async def run() -> None:
            try:
                await self.align(job_id, english_source_id, polish_source_id, mode)
            except UserInputError as exc:
                with self._lock, self._connect() as db:
                    db.execute("UPDATE jobs SET error_message=? WHERE id=?", (str(exc), job_id))
                await self._emit(job_id, "ERROR", JobStatus.FAILED, str(exc), 100)
            except Exception as exc:
                message = f"Synchronizacja nie powiodła się ({type(exc).__name__})"
                with self._lock, self._connect() as db:
                    db.execute("UPDATE jobs SET error_message=? WHERE id=?", (message, job_id))
                await self._emit(job_id, "ERROR", JobStatus.FAILED, message, 100)
        asyncio.create_task(run())

    async def align(self, job_id: str, english_source_id: str | None, polish_source_id: str | None,
                    mode: AlignmentMode = AlignmentMode.SEMANTIC_PREFERRED) -> None:
        job = self.get(job_id)
        if not job or not job.get("report"):
            raise UserInputError("Najpierw wykonaj analizę materiału")
        report = job["report"]
        english_options = report.get("englishRanking") or []
        polish_options = report.get("polishRanking") or []
        def source_id(item: dict) -> str:
            return f"{item.get('sourceType')}:{item.get('streamIndex', item.get('name'))}"
        def choose(options: list[dict], requested: str | None, selected: dict | None) -> dict | None:
            wanted = requested or (source_id(selected) if selected else None)
            return next((item for item in options if source_id(item) == wanted), None)
        english = choose(english_options, english_source_id, report.get("selectedEnglish"))
        polish = choose(polish_options, polish_source_id, report.get("selectedPolish"))
        if english_source_id and not english or polish_source_id and not polish:
            raise UserInputError("Wybrane źródło nie należy do raportu zadania")
        await self._emit(job_id, "INFO", JobStatus.SELECTING_SOURCES,
                         f"Źródła: EN {candidate_label(english)}, PL {candidate_label(polish)}", 5)
        if not english or not polish:
            raise UserInputError("Brak kompletnej pary angielskich i polskich napisów")
        if english.get("type") == "graphic":
            report["alignment"] = {"status": "NEEDS_OCR", "quality": "UNUSABLE",
                                   "warnings": ["Angielskie źródło jest graficzne i wymaga OCR"],
                                   "selectedEnglish": english, "selectedPolish": polish}
            self._save_report(job_id, report, job.get("resolved_media_path") or job["media_path"])
            await self._emit(job_id, "WARNING", JobStatus.NEEDS_OCR, "Wybrane napisy graficzne wymagają OCR", 100)
            return
        english_path = Path(report.get("workingFiles", {}).get("englishReference") or "")
        polish_path = Path(polish.get("path") or "")
        if not english_path.is_file() or polish.get("sourceType") != "external" or not polish_path.is_file():
            raise UserInputError("Etap 3 wymaga tekstowej kopii angielskiej i zewnętrznego polskiego pliku")
        await self._emit(job_id, "INFO", JobStatus.PARSING_SUBTITLES, "Parsowanie napisów do modelu milisekundowego", 15)
        english_cues, polish_cues = await asyncio.gather(
            asyncio.to_thread(parse_cues, english_path, "english"), asyncio.to_thread(parse_cues, polish_path, "polish"))
        duration_ms = round((report.get("media", {}).get("durationSeconds") or 0) * 1000)
        await self._emit(job_id, "INFO", JobStatus.BUILDING_ANCHORS, "Budowanie strukturalnych punktów dopasowania", 30)
        structural = StructuralAnchorProvider().provide(english_cues, polish_cues, duration_ms, {"english": english, "polish": polish})
        anchors = structural
        structural_model = select_model(fit_models(
            structural, self.settings.alignment_min_scale, self.settings.alignment_max_scale,
            self.settings.alignment_max_segments, self.settings.alignment_min_points_per_segment
        ))
        semantic_report = {"mode": mode, "enabled": self.settings.openai_semantic_alignment_enabled,
                           "configured": self.settings.openai_configured, "fallbackUsed": False,
                           "model": self.settings.openai_model, "promptVersion": "semantic-anchor-v1",
                           "qualityBefore": quality(structural_model)}
        semantic_allowed = self.settings.openai_semantic_alignment_enabled and self.settings.openai_configured
        if mode != AlignmentMode.STRUCTURAL_ONLY and semantic_allowed:
            await self._emit(job_id, "INFO", JobStatus.PREPARING_SEMANTIC_WINDOWS,
                             "Przygotowanie prywatnych okien semantycznych — przebieg 1/2", 34)
            try:
                await self._emit(job_id, "INFO", JobStatus.REQUESTING_SEMANTIC_ANCHORS,
                                 "Żądanie semantycznych relacji EN–PL przez Responses API", 38)
                async def semantic_progress(pass_number: int) -> None:
                    if pass_number == 2:
                        await self._emit(job_id, "INFO", JobStatus.REFINING_SEMANTIC_ANCHORS,
                                         "Doprecyzowanie kotwic semantycznych — przebieg 2/2", 40)
                semantic = await OpenAIAnchorProvider(self.settings).provide_async(
                    english_cues, polish_cues, duration_ms,
                    {"english": english, "polish": polish, "structural": structural}, semantic_progress)
                await self._emit(job_id, "INFO", JobStatus.VALIDATING_SEMANTIC_ANCHORS,
                                 f"Zatwierdzono {len(semantic.anchors)}, odrzucono {len(semantic.rejected)} kotwic", 42)
                anchors = CompositeAnchorProvider.combine(structural, semantic.anchors)
                semantic_report.update({"usage": semantic.telemetry.to_dict(), "acceptedRelations": semantic.accepted,
                                        "rejectedRelations": semantic.rejected,
                                        "finalAnchors": [{"referenceTime": a.reference_time, "sourceTime": a.source_time,
                                                          "weight": a.confidence, "origin": a.origin} for a in semantic.anchors]})
            except SemanticBudgetExceeded as exc:
                semantic_report.update({"error": "AI_BUDGET_EXCEEDED", "message": str(exc)})
                if mode == AlignmentMode.SEMANTIC_REQUIRED:
                    report["semanticAlignment"] = semantic_report
                    self._save_report(job_id, report, job.get("resolved_media_path") or job["media_path"])
                    await self._emit(job_id, "WARNING", JobStatus.AI_BUDGET_EXCEEDED, "Wyczerpano budżet żądań AI", 100)
                    return
                semantic_report["fallbackUsed"] = True
                await self._emit(job_id, "WARNING", JobStatus.SEMANTIC_FALLBACK, "Budżet AI wyczerpany — fallback strukturalny", 44)
            except (SemanticUnavailable, SemanticBatchError) as exc:
                semantic_report.update({"error": "AI_UNAVAILABLE", "message": str(exc)})
                if mode == AlignmentMode.SEMANTIC_REQUIRED:
                    report["semanticAlignment"] = semantic_report
                    self._save_report(job_id, report, job.get("resolved_media_path") or job["media_path"])
                    await self._emit(job_id, "WARNING", JobStatus.AI_UNAVAILABLE, "OpenAI jest niedostępne dla tego zadania", 100)
                    return
                semantic_report["fallbackUsed"] = True
                await self._emit(job_id, "WARNING", JobStatus.SEMANTIC_FALLBACK, "OpenAI niedostępne — fallback strukturalny", 44)
        elif mode != AlignmentMode.STRUCTURAL_ONLY:
            semantic_report.update({"error": "NOT_CONFIGURED", "fallbackUsed": mode == AlignmentMode.SEMANTIC_PREFERRED})
            if mode == AlignmentMode.SEMANTIC_REQUIRED:
                report["semanticAlignment"] = semantic_report
                self._save_report(job_id, report, job.get("resolved_media_path") or job["media_path"])
                await self._emit(job_id, "WARNING", JobStatus.AI_UNAVAILABLE,
                                 "Semantyczne dopasowanie jest wyłączone lub brak klucza", 100)
                return
            await self._emit(job_id, "WARNING", JobStatus.SEMANTIC_FALLBACK,
                             "Semantyka nieaktywna — użyto kotwic strukturalnych", 44)
        await self._emit(job_id, "INFO", JobStatus.FITTING_MODELS, f"Dopasowanie modeli do {len(anchors)} punktów", 45)
        models = fit_models(anchors, self.settings.alignment_min_scale, self.settings.alignment_max_scale,
                            self.settings.alignment_max_segments, self.settings.alignment_min_points_per_segment)
        await self._emit(job_id, "INFO", JobStatus.SELECTING_STRATEGY, "Deterministyczny wybór najprostszego wiarygodnego modelu", 60)
        model = select_model(models); grade = quality(model)
        semantic_report["qualityAfter"] = grade
        if not model:
            grade = "UNUSABLE"
            alignment = {"status": "REVIEW_REQUIRED", "quality": grade, "warnings": ["Za mało wiarygodnych punktów"],
                         "selectedEnglish": english, "selectedPolish": polish, "anchorCount": len(anchors)}
        else:
            await self._emit(job_id, "INFO", JobStatus.TRANSFORMING_TIMESTAMPS,
                             f"Transformacja: {model['strategy']}, offset {model.get('offsetMs')} ms, scale {model.get('scale')}", 72)
            transformed, validation = transform(polish_cues, model, duration_ms, self.settings.alignment_end_tolerance_ms)
            await self._emit(job_id, "INFO", JobStatus.VALIDATING_OUTPUT, "Walidacja kolejności i zakresu czasów", 84)
            preview = self.settings.data_root / "work" / "jobs" / job_id / "preview.AI-Sync.pl.srt"
            input_hash = sha256(polish_path)
            await self._emit(job_id, "INFO", JobStatus.GENERATING_PREVIEW, "Atomowy zapis podglądu UTF-8 w katalogu roboczym", 92)
            write_preview(transformed, preview)
            warnings = []
            if validation["reversedSegments"] or validation["overlappingSegments"]: warnings.append("Wynik zawiera konflikty czasowe")
            status = "COMPLETED" if grade in {"HIGH", "MEDIUM"} and not warnings else "REVIEW_REQUIRED"
            alignment = {"status": status, "quality": grade, "model": public_model(model),
                         "models": [public_model(item) for item in models], "anchorCount": len(anchors),
                         "anchorSummary": [{"referenceTime": a.reference_time, "sourceTime": a.source_time,
                                            "confidence": a.confidence, "origin": a.origin} for a in anchors],
                         "validation": validation, "firstBeforeMs": polish_cues[0].start_ms if polish_cues else None,
                         "lastBeforeMs": polish_cues[-1].end_ms if polish_cues else None,
                         "firstAfterMs": transformed[0].start_ms if transformed else None,
                         "lastAfterMs": transformed[-1].end_ms if transformed else None,
                         "inputSha256": input_hash, "previewSha256": sha256(preview), "previewPath": str(preview),
                         "mediaIdentity": identity(Path(job.get("resolved_media_path") or job["media_path"])),
                         "selectedEnglish": english, "selectedPolish": polish, "warnings": warnings,
                         "readyForPublication": status == "COMPLETED", "mediaDirectoryModified": False}
        report["alignment"] = alignment
        report["semanticAlignment"] = semantic_report
        self._save_report(job_id, report, job.get("resolved_media_path") or job["media_path"])
        terminal = JobStatus.COMPLETED if alignment["status"] == "COMPLETED" else JobStatus.REVIEW_REQUIRED
        if terminal == JobStatus.COMPLETED and self.settings.subtitle_agent_publish_enabled:
            if self.settings.subtitle_agent_publish_mode == "MANUAL":
                terminal = JobStatus.READY_TO_PUBLISH
            elif self.settings.subtitle_agent_publish_mode == "AUTO_HIGH":
                try:
                    await self.publish(job_id, True, alignment["previewSha256"], automatic=True)
                except PublishError:
                    pass
                return
        await self._emit(job_id, "SUCCESS" if terminal == JobStatus.COMPLETED else "WARNING", terminal,
                         f"Synchronizacja: {alignment['quality']} — plik pozostaje tylko podglądem", 100)

    async def _build_workpack(self, job_id: str, requirements: PipelineRequirements,
                              cached: dict | None = None, requested_reference: str | None = None) -> None:
        job = self.get(job_id)
        task_type = WorkpackTaskType(job.get("task_type") or WorkpackTaskType.SYNC_AND_LANGUAGE_REVIEW)
        await self._emit(job_id, "INFO", JobStatus.VALIDATING_PATH, "Bezpieczna weryfikacja ścieżki", 5)
        media_path = validate_media_path(job["media_path"], self.settings.media_roots)
        job_dir = self.settings.data_root / "work" / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        if cached:
            media = cached["media"]; external = cached["externalSubtitles"]
            rejected_external = cached.get("rejectedSubtitleCandidates", [])
            english_ranking = cached["englishRanking"]; polish_ranking = cached["polishRanking"]
            await self._emit(job_id, "INFO", JobStatus.PROBING_MEDIA,
                             "Użyto zapisanej analizy ffprobe; źródło nie było ponownie sondowane", 15)
        else:
            await self._emit(job_id, "INFO", JobStatus.PROBING_MEDIA, "Analiza techniczna materiału przez ffprobe", 15)
            media = await probe_media(media_path, self.settings.ffprobe_timeout_seconds)
            media.setdefault("identity", parse_media_identity(media_path.name).model_dump(mode="json"))
            await self._emit(job_id, "INFO", JobStatus.DISCOVERING_SUBTITLES, "Wykrywanie osadzonych i zewnętrznych napisów", 25)
            external, rejected_external = await asyncio.to_thread(discover_external_subtitles_with_rejections, media_path)
            english_ranking = rank_english(media["embeddedSubtitles"], external)
            polish_ranking = rank_polish(media, external, media["embeddedSubtitles"])
        await self._emit(job_id, "INFO", JobStatus.SELECTING_REFERENCE, "Ranking angielskich źródeł referencyjnych", 32)
        selected = None
        if requested_reference:
            selected = next((item for item in english_ranking
                             if f"{item.get('sourceType')}:{item.get('streamIndex')}" == requested_reference), None)
        else:
            eligible_english = [item for item in english_ranking
                                if not requirements.require_text_english or item.get("type") == "text"]
            if eligible_english and eligible_english[0].get("score", 0) > 0:
                selected = eligible_english[0]
        margin = self.settings.workpack_reference_score_margin
        ambiguous = bool(selected and not requested_reference and len(english_ranking) > 1 and
                         selected.get("score", 0) - english_ranking[1].get("score", 0) < margin)
        alternatives = []
        if ambiguous and self.settings.workpack_include_reference_alternatives:
            alternatives = [item for item in english_ranking if item is not selected][
                :self.settings.workpack_max_reference_alternatives]
            await self._emit(job_id, "WARNING", JobStatus.REFERENCE_AMBIGUOUS,
                             f"Wybór niejednoznaczny: różnica jest mniejsza niż {margin} punktów", 36)
        warnings: list[str] = []
        if ambiguous: warnings.append("Wybór angielskiej referencji jest niejednoznaczny")
        if not selected:
            warnings.append("Nie znaleziono wiarygodnej angielskiej referencji")
            await self._emit(job_id, "WARNING", JobStatus.NO_ENGLISH_REFERENCE, warnings[-1], 40)
        reference_files: list[Path] = []
        if (selected and requirements.extract_reference and
                (not requirements.require_text_english or selected.get("type") == "text")):
            await self._emit(job_id, "INFO", JobStatus.EXTRACTING_REFERENCE,
                             f"Ekstrakcja strumienia {selected.get('streamIndex')} ({selected.get('codec')})", 43)
            reference_files = await extract_embedded(selected, media_path, job_dir / "reference" / "selected",
                                                     self.settings.ffmpeg_timeout_seconds)
        alternative_files: list[Path] = []
        for number, alternative in enumerate(alternatives if requirements.extract_reference else [], 1):
            extracted = await extract_embedded(alternative, media_path,
                                               job_dir / "reference" / "alternatives" / f"source-{number:03d}",
                                               self.settings.ffmpeg_timeout_seconds)
            for path in extracted:
                variant = path.name.removeprefix("selected")
                destination = job_dir / "reference" / "alternatives" / f"alternative-{number:03d}{variant}"
                destination.parent.mkdir(parents=True, exist_ok=True); path.replace(destination); alternative_files.append(destination)
        polish: list[dict] = []
        omitted_polish: list[dict] = []
        if requirements.copy_polish:
            await self._emit(job_id, "INFO", JobStatus.COLLECTING_POLISH_CANDIDATES,
                             "Kopiowanie prawidłowo dopasowanych polskich kandydatów byte-for-byte", 58)
            polish, omitted_polish = await asyncio.to_thread(copy_polish_candidates, polish_ranking, job_dir / "polish",
                                                             self.settings.workpack_max_polish_candidates)
        else:
            await self._emit(job_id, "INFO", JobStatus.COLLECTING_POLISH_CANDIDATES,
                             f"Pipeline {requirements.name}: materiały PL pozostają wyłącznie w raporcie", 58)
        if omitted_polish: warnings.append(f"Pominięto {len(omitted_polish)} kandydatów z powodu limitu")
        await self._emit(job_id, "INFO", JobStatus.BUILDING_TIMELINES, "Budowanie technicznych timeline'ów", 68)
        selected_srt = next((path for path in reference_files if path.name == "selected.eng.srt"), None)
        reference_timeline = timeline(selected_srt, "reference") if selected_srt else None
        pgs_timeline = None
        if selected and selected.get("type") == "graphic":
            pgs_timeline = await graphic_timeline(media_path, int(selected["streamIndex"]), self.settings.ffprobe_timeout_seconds)
        polish_timelines = {item["archiveName"]: timeline(job_dir / item["archiveName"], item["archiveName"])
                            for item in polish if Path(item["archiveName"]).suffix.lower() == ".srt"}
        for item in polish:
            details = polish_timelines.get(item["archiveName"], {})
            item.update({"encoding": (item.get("analysis") or {}).get("encoding"),
                         "cueCount": details.get("cue_count"), "firstMs": details.get("first_ms"),
                         "lastMs": details.get("last_ms"), "coverage": (details.get("last_ms") or 0) /
                         max(1, round((media.get("durationSeconds") or 0) * 1000)),
                         "parserWarnings": details.get("warnings", [])})
        analysis = job_dir / "analysis"
        write_json(analysis / "media-summary.json", media_summary(media))
        write_json(analysis / "subtitle-streams.json", subtitle_streams(media))
        ranking_safe = [{key: item.get(key) for key in ("streamIndex", "codec", "language", "title", "type", "score", "reasons", "default", "forced", "hearingImpaired")}
                        for item in english_ranking]
        write_json(analysis / "source-ranking.json", {"english": ranking_safe,
                   "polish": [{"name": item.get("name"), "score": item.get("score"), "reasons": item.get("reasons"),
                               "matchConfidence": item.get("matchConfidence"), "matchReasons": item.get("matchReasons"),
                               "matchAutomatic": item.get("matchAutomatic")} for item in polish_ranking]})
        if reference_timeline: write_json(analysis / "reference-timeline.json", reference_timeline)
        if pgs_timeline: write_json(analysis / "reference-pgs-timeline.json", pgs_timeline)
        write_json(analysis / "polish-timelines.json", polish_timelines)
        hypotheses = (diagnostic_hypotheses(selected_srt, polish, job_dir,
                                            round((media.get("durationSeconds") or 0) * 1000))
                      if requirements.build_hypotheses else [])
        write_json(analysis / "synchronization-hypotheses.json", hypotheses)
        await self._emit(job_id, "INFO", JobStatus.BUILDING_MANIFEST, "Tworzenie manifestu bez ścieżek hosta", 78)
        reference_entry = None
        if selected:
            reference_entry = {key: selected.get(key) for key in ("streamIndex", "codec", "type", "language", "title", "default", "forced", "hearingImpaired", "score", "reasons")}
            reference_entry.update({"confidence": "AMBIGUOUS" if ambiguous else "RECOMMENDED",
                                    "files": [{"name": path.relative_to(job_dir).as_posix(), "sha256": sha256_file(path)} for path in reference_files],
                                    "cueCount": (reference_timeline or pgs_timeline or {}).get("cue_count", (pgs_timeline or {}).get("event_count")),
                                    "firstMs": (reference_timeline or {}).get("first_ms"), "lastMs": (reference_timeline or {}).get("last_ms")})
        expected = f"{safe_filename(media_path.stem)}.AI-Reviewed-v001.pl.srt"
        manifest = {"schema_version": SCHEMA_VERSION, "job_id": job_id, "task_type": task_type.value,
                    "media": media_summary(media), "reference": reference_entry,
                    "reference_alternatives": ranking_safe[1:1 + len(alternatives)],
                    "polish_candidates": [{key: item.get(key) for key in ("archiveName", "originalName", "languageHint", "encoding", "sizeBytes", "cueCount", "firstMs", "lastMs", "coverage", "parserWarnings", "score", "sha256", "generatedResult", "matchConfidence", "matchReasons", "matchAutomatic")} for item in polish],
                    "omitted_polish_candidates": [{"originalName": item.get("name"), "reason": item.get("omissionReason")} for item in omitted_polish],
                    "timing_analysis": {"hypothesisCount": len(hypotheses)},
                    "expected_output": {"filename": expected, "encoding": "UTF-8", "format": "SRT",
                                        "preserve_timing": True, "modify_media": False},
                    "warnings": warnings, "files": []}
        (job_dir / "REQUEST.md").write_text(request_text(task_type, manifest), encoding="utf-8")
        recommended = reference_files[0].relative_to(job_dir).as_posix() if reference_files else "brak"
        polish_names = ", ".join(item["archiveName"] for item in polish) or "brak"
        (job_dir / "README.txt").write_text(
            f"Subtitle Agent workpack\nReferencja: {recommended}\nPolscy kandydaci: {polish_names}\n"
            f"Typ referencji: {(selected or {}).get('type', 'brak')}\nWybór jednoznaczny: {'nie' if ambiguous else 'tak'}\n"
            "Prześlij najlepiej całe archiwum ZIP do ChatGPT wraz z REQUEST.md.\n", encoding="utf-8")
        manifest["files"] = sorted(["manifest.json", "checksums.sha256"] + [
            path.relative_to(job_dir).as_posix() for path in job_dir.rglob("*")
            if path.is_file() and path.suffix != ".zip" and path.name not in {"manifest.json", "checksums.sha256"}])
        write_json(job_dir / "manifest.json", manifest)
        await self._emit(job_id, "INFO", JobStatus.BUILDING_WORKPACK, "Pakowanie ZIP i obliczanie SHA-256", 90)
        archive, version, archive_hash, omitted_files = await asyncio.to_thread(
            build_zip, job_dir, media_path.stem, self.settings.workpack_max_archive_bytes, self.settings.workpack_max_files)
        if omitted_files:
            warnings.append(f"Pominięto {len(omitted_files)} plików z powodu limitu archiwum")
        workpack = {"schemaVersion": SCHEMA_VERSION, "version": version, "path": str(archive),
                    "filename": archive.name, "sizeBytes": archive.stat().st_size, "sha256": archive_hash,
                    "files": manifest["files"], "warnings": warnings, "omittedFiles": omitted_files,
                    "referenceAmbiguous": ambiguous}
        blocking_requirements: list[str] = []
        if requirements.require_english and not requirements.require_text_english and not selected:
            blocking_requirements.append("Brak wymaganej angielskiej referencji")
        if requirements.require_text_english and (not selected or selected.get("type") != "text" or not selected_srt):
            blocking_requirements.append("Brak wymaganej tekstowej referencji angielskiej")
        if requirements.require_polish and not polish:
            blocking_requirements.append("Brak prawidłowo dopasowanego kandydata polskiego")
        duration_seconds = media.get("durationSeconds") or 0
        inspected_polish = []
        for item in polish_ranking:
            if item.get("sourceType") != "external":
                continue
            analysis_data = item.get("analysis") or {}
            language = (item.get("languageHint") or analysis_data.get("detected_language") or "").casefold()
            if language not in {"pl", "pol", "polish"}:
                continue
            inspected_polish.append({
                "name": item.get("name"), "score": item.get("score"), "rankingReasons": item.get("reasons", []),
                "matchConfidence": item.get("matchConfidence"), "matchReasons": item.get("matchReasons", []),
                "matchAutomatic": item.get("matchAutomatic"), "segments": analysis_data.get("segment_count"),
                "firstTimestamp": analysis_data.get("first_time"), "lastTimestamp": analysis_data.get("last_time"),
                "movieCoverage": ((analysis_data.get("last_time") or 0) / duration_seconds if duration_seconds else None),
                "structuralErrors": {
                    "malformedSegments": analysis_data.get("malformed_segments", 0),
                    "reversedIntervals": analysis_data.get("reversed_intervals", 0),
                    "overlappingSegments": analysis_data.get("overlapping_segments", 0),
                    "monotonic": analysis_data.get("monotonic"),
                    "warnings": analysis_data.get("warnings", []),
                },
            })
        report = {"reportVersion": 2, "pipeline": requirements.name,
                  "jobType": "PREPARE_WORKPACK", "taskType": task_type.value, "media": media,
                  "mediaInspection": media_summary(media),
                  "externalSubtitles": external, "englishRanking": english_ranking, "polishRanking": polish_ranking,
                  "embeddedSubtitleTracks": media.get("embeddedSubtitles", []),
                  "polishCandidateInspection": inspected_polish,
                  "rejectedSubtitleCandidates": rejected_external,
                  "rejectedPolishCandidates": [item for item in rejected_external
                                                if item.get("languageHint") in {"pl", "pol", "polish"}],
                  "selectedEnglish": selected, "referenceAlternatives": alternatives,
                  "polishCandidates": polish, "synchronizationHypotheses": hypotheses,
                  "workpack": workpack, "warnings": warnings, "incompleteReasons": blocking_requirements,
                  "mediaDirectoryModified": False}
        self._save_report(job_id, report, str(media_path))
        terminal = JobStatus.WORKPACK_INCOMPLETE if blocking_requirements else JobStatus.WORKPACK_READY
        await self._emit(job_id, "WARNING" if terminal == JobStatus.WORKPACK_INCOMPLETE else "SUCCESS", terminal,
                         f"Workpack gotowy: {archive.name} ({archive.stat().st_size} B)", 100)

    async def _worker(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                job = self.get(job_id)
                if job.get("job_type") == "PREPARE_WORKPACK":
                    task_type = WorkpackTaskType(job.get("task_type") or WorkpackTaskType.SYNC_AND_LANGUAGE_REVIEW)
                    await workpack_service(task_type).prepare(self, job_id)
                    continue
                await self._emit(job_id, "INFO", JobStatus.VALIDATING_PATH, "Bezpieczna weryfikacja ścieżki", 10)
                media_path = validate_media_path(job["media_path"], self.settings.media_roots)
                work_dir = self.settings.data_root / "work" / "jobs" / job_id
                work_dir.mkdir(parents=True, exist_ok=False)

                await self._emit(job_id, "INFO", JobStatus.PROBING_MEDIA, "Analiza techniczna pliku przez ffprobe", 30)
                media = await probe_media(media_path, self.settings.ffprobe_timeout_seconds)

                await self._emit(job_id, "INFO", JobStatus.DISCOVERING_SUBTITLES, "Wyszukiwanie wbudowanych i zewnętrznych napisów", 50)
                external = await asyncio.to_thread(discover_external_subtitles, media_path)
                embedded = media["embeddedSubtitles"]
                await self._emit(
                    job_id, "INFO", JobStatus.DISCOVERING_SUBTITLES,
                    f"Znaleziono: {len(embedded)} wbudowanych ścieżek napisów i {len(external)} plików zewnętrznych", 55,
                )

                await self._emit(job_id, "INFO", JobStatus.ANALYZING_CANDIDATES, "Klasyfikacja źródeł angielskich i polskich", 70)
                english_ranking = rank_english(embedded, external)
                polish_ranking = rank_polish(media, external, embedded)
                selected_english = english_ranking[0] if english_ranking and english_ranking[0]["score"] > 0 else None
                selected_polish = next((item for item in polish_ranking if item["eligibleByDefault"] and item["score"] > 0), None)
                await self._emit(job_id, "INFO", JobStatus.ANALYZING_CANDIDATES,
                                 f"Wybrane źródło angielskie: {candidate_label(selected_english)}", 75)
                await self._emit(job_id, "SUCCESS" if selected_polish else "WARNING", JobStatus.ANALYZING_CANDIDATES,
                                 f"Wybrane polskie napisy: {candidate_label(selected_polish)}", 80)

                extraction_message = (
                    f"Ekstrakcja {candidate_label(selected_english)} do katalogu roboczego; "
                    f"limit {self.settings.ffmpeg_timeout_seconds:g} s — ffmpeg może czytać cały plik"
                    if selected_english else "Pominięto ekstrakcję: brak angielskiego źródła referencyjnego"
                )
                await self._emit(job_id, "INFO" if selected_english else "WARNING",
                                 JobStatus.EXTRACTING_REFERENCE, extraction_message, 85)
                try:
                    working_reference = await extract_reference(
                        selected_english, media_path, work_dir, self.settings.ffmpeg_timeout_seconds
                    )
                except ProcessTimeoutError:
                    message = (
                        f"Ekstrakcja przez ffmpeg przekroczyła limit {self.settings.ffmpeg_timeout_seconds:g} s. "
                        "Plik może być duży lub odczyt z magazynu sieciowego jest zbyt wolny."
                    )
                    with self._lock, self._connect() as db:
                        db.execute("UPDATE jobs SET error_message=? WHERE id=?", (message, job_id))
                    await self._emit(job_id, "ERROR", JobStatus.FAILED, message, 100)
                    continue
                except ProcessExecutionError as exc:
                    message = f"ffmpeg nie ukończył ekstrakcji: {exc}"
                    with self._lock, self._connect() as db:
                        db.execute("UPDATE jobs SET error_message=? WHERE id=?", (message, job_id))
                    await self._emit(job_id, "ERROR", JobStatus.FAILED, message, 100)
                    continue
                if working_reference:
                    await self._emit(job_id, "SUCCESS", JobStatus.EXTRACTING_REFERENCE,
                                     f"Kopia robocza jest gotowa: {Path(working_reference).name}", 92)
                warnings = []
                if selected_english and selected_english.get("type") == "graphic":
                    warnings.append("Wybrana angielska ścieżka jest graficzna i w przyszłości będzie wymagać OCR")
                if not selected_english:
                    warnings.append("Nie znaleziono wiarygodnego angielskiego źródła referencyjnego")
                if not selected_polish:
                    warnings.append("Nie znaleziono wiarygodnych polskich napisów")
                report = {
                    "media": media, "externalSubtitles": external,
                    "englishRanking": english_ranking, "polishRanking": polish_ranking,
                    "selectedEnglish": selected_english, "selectedPolish": selected_polish,
                    "workingFiles": {"englishReference": working_reference}, "warnings": warnings,
                    "mediaDirectoryModified": False,
                }
                self._save_report(job_id, report, str(media_path))
                await self._emit(job_id, "SUCCESS", JobStatus.COMPLETED, "Analiza materiału zakończona", 100)
            except UserInputError as exc:
                with self._lock, self._connect() as db:
                    db.execute("UPDATE jobs SET error_message=? WHERE id=?", (str(exc), job_id))
                await self._emit(job_id, "ERROR", JobStatus.FAILED, str(exc), 100)
            except Exception as exc:
                safe_error = f"Analiza nie powiodła się ({type(exc).__name__})"
                with self._lock, self._connect() as db:
                    db.execute("UPDATE jobs SET error_message=? WHERE id=?", (safe_error, job_id))
                await self._emit(job_id, "ERROR", JobStatus.FAILED, safe_error, 100)
            finally:
                self._queue.task_done()

    async def stream(self, job_id: str, after: int = 0) -> AsyncIterator[str]:
        condition = self._conditions.setdefault(job_id, asyncio.Condition())
        while True:
            pending = [event for event in self.events(job_id) if event.sequence > after]
            for event in pending:
                after = event.sequence
                yield f"id: {event.sequence}\nevent: job\ndata: {event.model_dump_json(by_alias=True)}\n\n"
            job = self.get(job_id)
            if job and JobStatus(job["status"]) in TERMINAL:
                break
            try:
                async with condition:
                    await asyncio.wait_for(condition.wait(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"
