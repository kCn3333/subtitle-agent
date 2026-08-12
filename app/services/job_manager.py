import asyncio
import json
import sqlite3
import threading
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from app.models.job import JobEvent, JobStatus


TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.INTERRUPTED}


def now() -> datetime:
    return datetime.now().astimezone()


class JobManager:
    def __init__(self, db_path: Path, max_concurrent: int, step_delay: float = 0.25):
        self.db_path = db_path
        self.max_concurrent = max_concurrent
        self.step_delay = step_delay
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
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error_message TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL, sequence INTEGER NOT NULL, timestamp TEXT NOT NULL,
                    level TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL,
                    progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
                    PRIMARY KEY(job_id, sequence), FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
            """)
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
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
                       (job_id, media_path, JobStatus.QUEUED, 0, stamp, None, None, None))
            self._insert_event(db, job_id, "INFO", JobStatus.QUEUED, "Zadanie zostało utworzone", 0)
        self._conditions[job_id] = asyncio.Condition()
        await self._queue.put(job_id)
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

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
        started = stamp if stage == JobStatus.INSPECTING else None
        finished = stamp if stage in TERMINAL else None
        with self._lock, self._connect() as db:
            self._insert_event(db, job_id, level, stage, message, progress)
            db.execute("UPDATE jobs SET status=?, progress=?, started_at=COALESCE(started_at,?), finished_at=COALESCE(?,finished_at) WHERE id=?",
                       (stage, progress, started, finished, job_id))
        condition = self._conditions.setdefault(job_id, asyncio.Condition())
        async with condition:
            condition.notify_all()

    async def _worker(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                steps = [
                    ("INFO", JobStatus.INSPECTING, "Sprawdzanie ścieżki do materiału", 20),
                    ("INFO", JobStatus.CHECKING_TOOLS, "Sprawdzanie ffmpeg i ffprobe", 50),
                    ("SUCCESS", JobStatus.READY, "Środowisko jest gotowe", 80),
                    ("SUCCESS", JobStatus.COMPLETED, "Zadanie demonstracyjne zakończone", 100),
                ]
                for step in steps:
                    await asyncio.sleep(self.step_delay)
                    await self._emit(job_id, *step)
            except Exception as exc:
                await self._emit(job_id, "ERROR", JobStatus.FAILED, f"Zadanie nie powiodło się: {type(exc).__name__}", 100)
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
