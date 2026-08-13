import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.jobs import router as jobs_router
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
        prepare_data_root(config.data_root)
        tools = probe_tools()
        logger.info("Narzędzia systemowe: %s; %s", tools.ffmpeg, tools.ffprobe)
        app.state.tools = tools
        app.state.jobs = JobManager(config.data_root / "subtitle-agent.db", config)
        await app.state.jobs.start()
        try:
            yield
        finally:
            await app.state.jobs.close()

    app = FastAPI(title=config.app_name, lifespan=lifespan)
    app.state.settings = config
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    templates = Jinja2Templates(directory="app/templates")

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        logger.exception("Nieobsłużony błąd", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "Wewnętrzny błąd serwera"}})

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"app_name": config.app_name})

    @app.get("/health")
    async def health(request: Request):
        return {"status": "ok", "ffmpeg": bool(request.app.state.tools.ffmpeg), "ffprobe": bool(request.app.state.tools.ffprobe)}

    app.include_router(jobs_router)
    return app


app = create_app()
