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


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.INTERRUPTED}


def now() -> datetime:
    return datetime.now().astimezone()


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
            stamp = now().isoformat()
            db.execute("UPDATE jobs SET status=?, finished_at=?, error_message=? WHERE status NOT IN (?,?,?)",
                       (JobStatus.INTERRUPTED, stamp, "Zadanie przerwane przez restart aplikacji",
                        JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.INTERRUPTED))

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

                await self._emit(job_id, "INFO", JobStatus.ANALYZING_CANDIDATES, "Klasyfikacja źródeł angielskich i polskich", 70)
                english_ranking = rank_english(media["embeddedSubtitles"], external)
                polish_ranking = rank_polish(media, external, media["embeddedSubtitles"])
                selected_english = english_ranking[0] if english_ranking and english_ranking[0]["score"] > 0 else None
                selected_polish = next((item for item in polish_ranking if item["eligibleByDefault"] and item["score"] > 0), None)

                await self._emit(job_id, "INFO", JobStatus.EXTRACTING_REFERENCE, "Przygotowanie bezpiecznej kopii roboczej źródła angielskiego", 85)
                working_reference = await extract_reference(
                    selected_english, media_path, work_dir, self.settings.ffmpeg_timeout_seconds
                )
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
