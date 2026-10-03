import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from semantic_worker.model import ConfigurationError, Encoder


class Segment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=8000)


class Batch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    segments: list[Segment] = Field(min_length=1, max_length=128)


def create_app(factory=Encoder):
    @asynccontextmanager
    async def lifespan(app):
        token_file = os.getenv('AI_TOKEN_FILE')
        app.state.token = Path(token_file).read_text().strip() if token_file else os.getenv('AI_TOKEN', '')
        if not app.state.token: raise RuntimeError('Worker token required')
        app.state.encoder = None; app.state.error = None; app.state.busy = False
        async def load():
            try: app.state.encoder = await asyncio.to_thread(factory)
            except ConfigurationError as exc: app.state.error = str(exc)
            except Exception: app.state.error = 'Model cache unavailable or incompatible; run explicit initialization'
        task = asyncio.create_task(load())
        yield
        await task
    app = FastAPI(lifespan=lifespan)

    @app.middleware('http')
    async def bounded_body(request, call_next):
        # Cap actual streamed bytes too; Content-Length alone is insufficient.
        if request.url.path == '/embeddings':
            total = 0; chunks = []
            try:
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        total += len(chunk)
                        if total > int(os.getenv('AI_MAX_REQUEST_BYTES', '1048576')):
                            from starlette.responses import JSONResponse
                            return JSONResponse({'detail': 'Request too large'}, status_code=413)
                        chunks.append(chunk)
            except TimeoutError:
                from starlette.responses import JSONResponse
                return JSONResponse({'detail': 'Body timeout'}, status_code=408)
            request._body = b''.join(chunks)
        return await call_next(request)

    def auth(request):
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer '+app.state.token):
            raise HTTPException(401, 'Unauthorized')

    @app.get('/health')
    async def health(): return {'status': 'ok'}

    @app.get('/ready')
    async def ready(request: Request):
        auth(request)
        if app.state.encoder is None: raise HTTPException(503, app.state.error or 'Loading model from cache')
        return {'ready': True, **app.state.encoder.metadata()}

    @app.post('/embeddings')
    async def embeddings(request: Request):
        auth(request)
        if app.state.encoder is None: raise HTTPException(503, 'Model unavailable')
        try: batch = Batch.model_validate(json.loads(await request.body()))
        except (ValueError, TypeError): raise HTTPException(422, 'Invalid batch')
        if len({x.id for x in batch.segments}) != len(batch.segments): raise HTTPException(422, 'Duplicate IDs')
        # Zero waiting slots: one active request. Busy clients use bounded retry.
        if app.state.busy: raise HTTPException(429, 'Worker busy; queue capacity is zero')
        app.state.busy = True
        async def compute():
            try: return await asyncio.to_thread(app.state.encoder.encode, [x.model_dump() for x in batch.segments])
            finally: app.state.busy = False
        task = asyncio.create_task(compute())
        # Shield keeps the slot occupied after timeout until the computation ends.
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(task), float(os.getenv('AI_REQUEST_TIMEOUT_SECONDS','120')))
        except TimeoutError: raise HTTPException(504, 'Inference timeout')
        except Exception: raise HTTPException(500, 'Embedding computation failed')
    return app


app = create_app()
