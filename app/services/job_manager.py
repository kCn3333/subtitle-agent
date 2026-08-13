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
from app.models.job import JobEvent, JobStatus
from app.services.media_analysis import (
    UserInputError, discover_external_subtitles, extract_reference, probe_media,
    rank_english, rank_polish, validate_media_path,
)
from app.services.process_runner import ProcessExecutionError, ProcessTimeoutError
from app.services.alignment import (StructuralAnchorProvider, fit_models, parse_cues, public_model,
                                    quality, select_model, sha256, transform, write_preview)


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.INTERRUPTED, JobStatus.REVIEW_REQUIRED, JobStatus.NEEDS_OCR}
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


class JobManager:
    def __init__(self, db_path: Path, settings: Settings):
        self.db_path = db_path
        self.settings = settings
        self.max_concurrent = settings.max_concurrent_jobs
        self._lock = threading.RLock()
        self._conditions: dict[str, asyncio.Condition] = {}
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
                    resolved_media_path TEXT, report_json TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL, sequence INTEGER NOT NULL, timestamp TEXT NOT NULL,
                    level TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL,
                    progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                    PRIMARY KEY(job_id, sequence), FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "resolved_media_path" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN resolved_media_path TEXT")
            if "report_json" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN report_json TEXT")
            for legacy, current in LEGACY_EVENT_STAGES.items():
                db.execute("UPDATE events SET stage=? WHERE stage=?", (current, legacy))
            stamp = now().isoformat()
            db.execute("UPDATE jobs SET status=?, finished_at=?, error_message=? WHERE status NOT IN (?,?,?,?,?)",
                       (JobStatus.INTERRUPTED, stamp, "Zadanie przerwane przez restart aplikacji",
                        JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.INTERRUPTED,
                        JobStatus.REVIEW_REQUIRED, JobStatus.NEEDS_OCR))

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

    async def start_alignment(self, job_id: str, english_source_id: str | None, polish_source_id: str | None) -> None:
        async def run() -> None:
            try:
                await self.align(job_id, english_source_id, polish_source_id)
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

    async def align(self, job_id: str, english_source_id: str | None, polish_source_id: str | None) -> None:
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
        anchors = StructuralAnchorProvider().provide(english_cues, polish_cues, duration_ms, {"english": english, "polish": polish})
        await self._emit(job_id, "INFO", JobStatus.FITTING_MODELS, f"Dopasowanie modeli do {len(anchors)} punktów", 45)
        models = fit_models(anchors, self.settings.alignment_min_scale, self.settings.alignment_max_scale,
                            self.settings.alignment_max_segments, self.settings.alignment_min_points_per_segment)
        await self._emit(job_id, "INFO", JobStatus.SELECTING_STRATEGY, "Deterministyczny wybór najprostszego wiarygodnego modelu", 60)
        model = select_model(models); grade = quality(model)
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
                         "selectedEnglish": english, "selectedPolish": polish, "warnings": warnings,
                         "readyForPublication": status == "COMPLETED", "mediaDirectoryModified": False}
        report["alignment"] = alignment
        self._save_report(job_id, report, job.get("resolved_media_path") or job["media_path"])
        terminal = JobStatus.COMPLETED if alignment["status"] == "COMPLETED" else JobStatus.REVIEW_REQUIRED
        await self._emit(job_id, "SUCCESS" if terminal == JobStatus.COMPLETED else "WARNING", terminal,
                         f"Synchronizacja: {alignment['quality']} — plik pozostaje tylko podglądem", 100)

    async def _worker(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                job = self.get(job_id)
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
