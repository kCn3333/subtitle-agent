"""One bounded AI worker; durable status, in-memory work, no automatic paid retries."""
import asyncio
import json
import logging
import sqlite3
import uuid
from contextlib import suppress
from datetime import datetime, timezone

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class QueueBusy(ValueError):
    pass


class JobBusy(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


class AiQueue:
    def __init__(self, path, jobs, states, capacity=5):
        self.path, self.jobs, self.states = path, jobs, states
        self.queue = asyncio.Queue(maxsize=capacity)
        self.pending = []
        self.operations = {}
        self.worker = None
        self.closed = False
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS ai_operations (id TEXT PRIMARY KEY, job_id TEXT, data TEXT NOT NULL)')
            for operation_id, job_id, raw in db.execute('SELECT id, job_id, data FROM ai_operations ORDER BY rowid'):
                state = json.loads(raw)
                if state['status'] in {'queued', 'running'}:
                    state.update(status='failed', phase='Operacja przerwana', finished_at=stamp(), http_status=503,
                                 error={'code':'INTERRUPTED', 'message':'Operacja AI została przerwana przez restart. Zleć ją ponownie, jeśli jest potrzebna.'})
                    db.execute('UPDATE ai_operations SET data=? WHERE id=?', (json.dumps(state), operation_id))
                self.operations[operation_id] = state
                if job_id:
                    states[job_id] = state

    def save(self, state):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute('INSERT INTO ai_operations VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (state['operation_id'], state.get('job_id'), json.dumps(state)))

    def status(self, operation_id):
        state = self.operations.get(operation_id)
        if state is None:
            return None
        result = dict(state)
        result.pop('request', None)
        if state['status'] == 'queued':
            result['queue_position'] = self.pending.index(operation_id) + 1 if operation_id in self.pending else 1
        return result

    def existing(self, job_id, intent):
        state = self.states.get(job_id)
        if state and state['status'] in {'queued', 'running'}:
            if state.get('request') == intent:
                return self.status(state['operation_id'])
            raise JobBusy('To zadanie ma już operację AI w kolejce lub w toku.')
        return None

    def submit(self, state, run):
        if self.closed or self.queue.full():
            raise QueueBusy('Kolejka AI jest pełna. Spróbuj ponownie po zakończeniu jednej z operacji.')
        job_id = state.get('job_id')
        if job_id:
            self.jobs.reserve_job(job_id, 'ai')
        try:
            state = {**state, 'operation_id':str(uuid.uuid4()), 'status':'queued', 'queued_at':stamp(),
                     'phase':'Oczekiwanie w kolejce AI', 'started_at':None}
            self.save(state)
            self.operations[state['operation_id']] = state
            if job_id:
                self.states[job_id] = state
            self.pending.append(state['operation_id'])
            self.queue.put_nowait((state, run))
        except BaseException:
            if job_id:
                self.jobs.release_job(job_id)
            raise
        return self.status(state['operation_id'])

    def forget_job(self, job_id):
        self.states.pop(job_id, None)
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute('DELETE FROM ai_operations WHERE job_id=?', (job_id,))
        for key in [key for key, value in self.operations.items() if value.get('job_id') == job_id]:
            self.operations.pop(key)

    def start(self):
        self.worker = asyncio.create_task(self._work())

    async def _work(self):
        while True:
            state, run = await self.queue.get()
            self.pending.remove(state['operation_id'])
            try:
                state.update(status='running', started_at=stamp(), phase='Przygotowanie danych dla modelu')
                self.save(state)
                result = await run()
                state.update(status='completed', result=result, http_status=200, phase='Gotowe')
                if state.get('batch') and result.get('failed_count'):
                    state.update(status='failed', http_status=502, phase='AI zakończone z błędami',
                                 error={'message': f"AI: {result['file_count']} gotowych plików, {result['failed_count']} błędnych. Możesz pobrać wynik i ponowić błędne pozycje."})
            except asyncio.CancelledError:
                state.update(status='failed', http_status=503, phase='Operacja przerwana',
                             error={'code':'INTERRUPTED', 'message':'Operacja AI została przerwana. Zleć ją ponownie, jeśli jest potrzebna.'})
                raise
            except HTTPException as exc:
                state.update(status='failed', error=exc.detail, http_status=exc.status_code, phase='Operacja zakończona błędem')
            except Exception:
                logger.exception('Operacja AI nie powiodła się')
                state.update(status='failed', http_status=500, phase='Operacja zakończona błędem',
                             error={'message':'Operacja AI nie powiodła się. Sprawdź logi serwera.'})
            finally:
                state['finished_at'] = stamp()
                try:
                    self.save(state)
                finally:
                    if state.get('job_id'):
                        self.jobs.release_job(state['job_id'])
                    self.queue.task_done()
                try:
                    await asyncio.to_thread(self.jobs.cleanup_expired_artifacts)
                except Exception:
                    logger.exception('Porządkowanie po operacji AI nie powiodło się')

    async def close(self):
        self.closed = True
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        while not self.queue.empty():
            state, _ = self.queue.get_nowait()
            self.pending.remove(state['operation_id'])
            state.update(status='failed', http_status=503, finished_at=stamp(), phase='Operacja przerwana',
                         error={'code':'INTERRUPTED', 'message':'Oczekiwanie przerwane przez zamknięcie aplikacji.'})
            try:
                self.save(state)
            finally:
                if state.get('job_id'):
                    self.jobs.release_job(state['job_id'])
                self.queue.task_done()
