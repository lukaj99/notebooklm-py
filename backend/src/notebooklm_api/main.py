"""FastAPI application factory and entry point."""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from notebooklm.exceptions import (
    AuthError,
    NotebookLMError,
    NotebookNotFoundError,
    RateLimitError,
    SourceNotFoundError,
    ValidationError,
)

from .config import settings
from .routes import artifacts, chat, notebooks, sources

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="NotebookLM API",
        version="0.1.0",
        description="REST API for Google NotebookLM automation",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(notebooks.router, prefix="/api/v1")
    app.include_router(sources.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(artifacts.router, prefix="/api/v1")

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, exc: AuthError):
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(NotebookNotFoundError)
    async def notebook_not_found_handler(request: Request, exc: NotebookNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(SourceNotFoundError)
    async def source_not_found_handler(request: Request, exc: SourceNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(request: Request, exc: RateLimitError):
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(NotebookLMError)
    async def notebooklm_error_handler(request: Request, exc: NotebookLMError):
        logger.error("NotebookLM error: %s", exc)
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()


def cli():
    """Run the API server (entry point for `notebooklm-api` command)."""
    import uvicorn

    uvicorn.run(
        "notebooklm_api.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    cli()
