"""
remuXcode - Unified Media Transcoding Service

FastAPI application serving the webhook API and SvelteKit frontend.
"""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from backend import __version__
from backend.core import cleanup_temp_dirs, initialize_components, shutdown_components

logger = logging.getLogger("remuxcode")

FRONTEND_BUILD = Path(__file__).parent.parent / "frontend" / "build"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle."""
    setup_logging()
    logger.info("=" * 60)
    logger.info("  remuXcode %s Starting", __version__)
    logger.info("=" * 60)
    cleanup_temp_dirs()
    initialize_components()
    yield
    shutdown_components()
    logger.info("remuXcode shut down")


def setup_logging() -> None:
    """Configure logging."""
    log_level_str = os.getenv("LOG_LEVEL", os.getenv("LOGLEVEL", "INFO")).upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    class ShortLoggerFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.name.startswith("backend.workers."):
                record.name = record.name.replace("backend.workers.", "")
            elif record.name.startswith("backend.utils."):
                record.name = record.name.replace("backend.utils.", "")
            elif record.name == "remuxcode":
                record.name = "main"
            return True

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    log_file = os.getenv("LOG_FILE", "/app/logs/remuxcode.log")
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    except (PermissionError, OSError):
        try:
            handlers.append(logging.FileHandler(os.path.expanduser("~/remuxcode.log")))
        except (PermissionError, OSError):
            pass

    log_filter = ShortLoggerFilter()
    for handler in handlers:
        handler.addFilter(log_filter)

    logging.basicConfig(
        level=log_level,
        format="%(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
    )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="remuXcode",
        description="Unified Media Transcoding Service for Sonarr/Radarr",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Import and include API routers
    from backend.api_browse import router as browse_router
    from backend.api_config import router as config_router
    from backend.api_convert import router as convert_router
    from backend.api_jobs import router as jobs_router
    from backend.api_webhook import router as webhook_router

    app.include_router(browse_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(convert_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(webhook_router, prefix="/api")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "healthy",
            "service": "remuxcode",
            "version": __version__,
        }

    # Serve SvelteKit frontend if build exists
    if FRONTEND_BUILD.is_dir():

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str) -> FileResponse:
            """Serve static files or SPA fallback for client-side routes."""
            file_path = (FRONTEND_BUILD / full_path).resolve()
            if (
                file_path.is_file()
                and file_path.is_relative_to(FRONTEND_BUILD.resolve())
            ):
                return FileResponse(file_path)
            return FileResponse(FRONTEND_BUILD / "index.html")
    else:
        # Fallback when no frontend build is present (dev mode)
        @app.get("/")
        async def root() -> dict[str, str]:
            return {
                "service": "remuxcode",
                "version": __version__,
                "message": "Frontend not built. Use /docs for API documentation.",
            }

    return app


app = create_app()
