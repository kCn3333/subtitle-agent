from app import __version__

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.auth import router as auth_router
from app.core.auth import AuthMiddleware
from app.services.auth import AuthStore
from app.services.operation_queue import AiQueue, QueueBusy, JobBusy
from app.api.jobs import router as jobs_router
from app.api.archive import router as archive_router
from app.api.ai_sync import router as ai_sync_router
from app.services.ai_sync import ApiSettingsStore
from app.api.app_settings import router as app_settings_router
from app.services.app_settings import AppSettingsStore
from app.services.ai_console import AiConsoleStore
from app.services.ai_health import ApiHealth
from app.api.workpacks import router as workpacks_router, tasks_router
from app.core.config import Settings, get_settings
from app.services.job_manager import JobManager
from app.services.system_probe import prepare_data_root, probe_tools

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("subtitle-agent")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.getLogger().setLevel(config.log_level.upper())
        config.require_auth_config()
        prepare_data_root(config.data_root)
        app.state.app_settings = AppSettingsStore(config.data_root / "subtitle-agent.db", config)
        app.state.auth = AuthStore(config.data_root / "subtitle-agent.db", config)
        tools = probe_tools()
        logger.info("Narzędzia systemowe: %s; %s; %s", tools.ffmpeg, tools.ffprobe, tools.mkvextract)
        app.state.tools = tools
        app.state.jobs = JobManager(config.data_root / "subtitle-agent.db", config)
        app.state.ai_settings = ApiSettingsStore(config.data_root / "subtitle-agent.db")
        app.state.ai_console = AiConsoleStore(config.data_root / "subtitle-agent.db")
        app.state.ai_console.usage.backfill(app.state.jobs.list_jobs(limit=None),
                                           config.data_root / "work" / "jobs")
        app.state.ai_queue = AiQueue(config.data_root / "subtitle-agent.db", app.state.jobs,
                                     app.state.ai_operations, config.max_queued_ai_operations)
        await app.state.jobs.start()
        app.state.ai_queue.start()
        try:
            yield
        finally:
            await app.state.ai_queue.close()
            await app.state.jobs.close()

    app = FastAPI(title=config.app_name, version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(AuthMiddleware, settings=config)
    app.state.settings = config
    app.state.ai_operations = {}
    app.state.ai_health = ApiHealth()
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    templates = Jinja2Templates(directory="app/templates")
    templates.env.globals["app_version"] = __version__

    @app.exception_handler(QueueBusy)
    async def queue_busy(_: Request, exc: QueueBusy):
        return JSONResponse(status_code=429, headers={"Retry-After":"30"},
                            content={"detail":{"code":"QUEUE_FULL", "message":str(exc)}})

    @app.exception_handler(JobBusy)
    async def job_busy(_: Request, exc: JobBusy):
        return JSONResponse(status_code=409, content={"detail":{"code":"JOB_BUSY", "message":str(exc)}})

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        logger.exception("Nieobsłużony błąd", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "Wewnętrzny błąd serwera"}})

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"app_name": config.app_name,
                                                                                       "app_mode": config.subtitle_agent_app_mode,
                                                                                       "ai_model": request.app.state.ai_settings.get().model})

    @app.get("/notifications-worker.js", include_in_schema=False)
    async def notifications_worker():
        return FileResponse("app/static/notifications-worker.js", media_type="application/javascript")

    @app.get("/health")
    async def health(request: Request):
        return {"status": "ok"}

    @app.get("/ai")
    async def ai_settings(request: Request):
        return templates.TemplateResponse(request=request, name="ai.html", context={"app_name": config.app_name})

    @app.get("/settings")
    async def app_settings(request: Request):
        return templates.TemplateResponse(request=request, name="settings.html", context={"app_name": config.app_name})

    @app.get("/archive")
    async def archive(request: Request):
        return templates.TemplateResponse(request=request, name="archive.html", context={"app_name": config.app_name})

    app.include_router(auth_router)
    app.include_router(archive_router)
    app.include_router(jobs_router)
    app.include_router(workpacks_router)
    app.include_router(tasks_router)
    app.include_router(ai_sync_router)
    app.include_router(app_settings_router)
    return app


app = create_app()
