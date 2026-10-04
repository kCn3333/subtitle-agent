"""Bounded diagnostic history of model replies, separate from subtitle results."""
import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from app.services.ai_sync import AiSyncError

_sink = ContextVar("ai_console_sink", default=None)
MAX_CHARS = 131072
MAX_ENTRIES = 100


class AiConsoleStore:
    def __init__(self, path: Path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ai_console (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                operation TEXT NOT NULL, job_id TEXT, level TEXT NOT NULL, message TEXT NOT NULL)''')

    def append(self, operation, job_id, level, message):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO ai_console(timestamp,operation,job_id,level,message) VALUES (?,?,?,?,?)",
                       (datetime.now(timezone.utc).isoformat(), operation, job_id, level, message))
            db.execute("DELETE FROM ai_console WHERE id NOT IN (SELECT id FROM ai_console ORDER BY id DESC LIMIT ?)",
                       (MAX_ENTRIES,))

    def read(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM ai_console ORDER BY id")]

    def clear(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM ai_console")


def emit(level, message):
    sink = _sink.get()
    if sink is not None:
        sink(level, message)


@contextmanager
def capture(store, settings, operation, job_id=None):
    key = settings.api_key.get_secret_value() if settings.api_key else ""

    def sink(level, value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        if key:
            # Redact both raw and JSON-escaped occurrences, including API error replies.
            for secret in {key, json.dumps(key, ensure_ascii=False)[1:-1],
                           json.dumps(key, ensure_ascii=True)[1:-1]}:
                text = text.replace(secret, "[UKRYTY KLUCZ API]")
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + f"\n[ODPOWIEDŹ SKRÓCONA: limit {MAX_CHARS} znaków]"
        store.append(operation, job_id, level, text)

    token = _sink.set(sink)
    try:
        emit("INFO", f"Wysyłanie żądania · model: {settings.model} · timeout: {settings.timeout_seconds} s")
        yield
    except AiSyncError as exc:
        emit("ERROR", str(exc))
        raise
    else:
        emit("SUCCESS", "Zakończono poprawnie")
    finally:
        _sink.reset(token)
