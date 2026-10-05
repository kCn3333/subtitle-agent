from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(prefix='/api/archive', tags=['archive'])


@router.get('')
async def archive(request: Request):
    manager = request.app.state.jobs
    with manager._lock:
        return {'titles': manager.archive.list_titles(manager.list_jobs(limit=None))}


@router.get('/files/{name}')
async def download(name: str, request: Request):
    manager = request.app.state.jobs
    with manager._lock:
        result = manager.archive.download(name)
    if result is None:
        raise HTTPException(404, detail='Nie znaleziono pliku w archiwum')
    path, filename = result
    return FileResponse(path, filename=filename,
                        media_type='application/zip' if path.suffix == '.zip' else 'application/x-subrip')
