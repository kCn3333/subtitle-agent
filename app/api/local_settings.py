"""Persist the local device choice. Credentials remain deployment secrets."""
import json
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.local_semantic import EmbeddingClient, LocalProtocolError, LocalUnavailable, MODEL, REVISION

router = APIRouter(prefix='/api/local-settings', tags=['local-settings'])


class LocalSettingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['disabled','cpu','lan_gpu']
    lanWorkerUrl: str | None = Field(default=None, max_length=2048)
    cpuFallbackEnabled: bool = False

    @field_validator('lanWorkerUrl')
    @classmethod
    def worker_url(cls, value):
        if not value or not value.strip(): return None
        value=value.strip().rstrip('/')
        parsed=urlsplit(value)
        try: port=parsed.port
        except ValueError: raise ValueError('Invalid worker port')
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError('Use worker origin URL without credentials, path or query')
        return value


def apply_settings(settings, saved):
    mode=saved['mode']
    settings.local_worker_url = settings.local_cpu_worker_url if mode=='cpu' else saved.get('lanWorkerUrl') if mode=='lan_gpu' else None
    settings.local_worker_expected_device = 'cpu' if mode=='cpu' else 'cuda' if mode=='lan_gpu' else None
    settings.local_cpu_fallback_enabled = saved.get('cpuFallbackEnabled',False) if mode=='lan_gpu' else False
    settings.local_cpu_fallback_url = settings.local_cpu_worker_url if settings.local_cpu_fallback_enabled else None


def current(manager):
    with manager._lock, manager._connect() as db:
        row=db.execute("SELECT value_json FROM app_settings WHERE key='local_ai'").fetchone()
    if row: return json.loads(row[0])
    settings=manager.settings
    mode='disabled' if not settings.local_worker_url else 'cpu' if settings.local_worker_url==settings.local_cpu_worker_url else 'lan_gpu'
    return {'mode':mode,'lanWorkerUrl':settings.local_worker_url if mode=='lan_gpu' else None,
            'cpuFallbackEnabled':settings.local_cpu_fallback_enabled}


@router.get('')
async def get_settings(request: Request):
    manager=request.app.state.jobs
    return {**current(manager),'model':MODEL,'revision':REVISION,'tokenConfigured':bool(manager.settings.local_worker_token),
            'cpuWorkerUrl':manager.settings.local_cpu_worker_url}


@router.put('')
async def save_settings(payload: LocalSettingsRequest, request: Request):
    if payload.mode=='lan_gpu' and not payload.lanWorkerUrl: raise HTTPException(422,'Podaj URL workera GPU w LAN')
    manager=request.app.state.jobs;data=payload.model_dump()
    with manager._lock, manager._connect() as db:
        db.execute("INSERT INTO app_settings(key,value_json) VALUES('local_ai',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                   (json.dumps(data),))
    apply_settings(manager.settings,data)
    return await get_settings(request)


@router.post('/check')
async def check_worker(payload: LocalSettingsRequest, request: Request):
    if payload.mode=='disabled':return {'available':False,'reason':'Lokalne AI wyłączone'}
    settings=request.app.state.jobs.settings.model_copy(deep=True)
    apply_settings(settings,payload.model_dump())
    try:
        metadata=await EmbeddingClient(settings).ready()
        return {'available':True,'worker':metadata}
    except (LocalUnavailable,LocalProtocolError) as exc:
        return {'available':False,'reason':str(exc)}
