from fastapi import APIRouter, HTTPException, Request

from app.services.app_settings import AppSettings

router = APIRouter(prefix='/api/settings/app', tags=['settings'])


@router.get('')
async def get_settings(request: Request):
    return request.app.state.app_settings.get()


@router.put('')
async def save_settings(payload: AppSettings, request: Request):
    try:
        return request.app.state.app_settings.save(payload)
    except ValueError as exc:
        raise HTTPException(422, detail={'message': str(exc)}) from exc
