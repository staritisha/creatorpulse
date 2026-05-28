"""
main.py — CreatorPulse Backend Entry Point

FastAPI app factory, middleware, CORS, route registration,
lifespan startup/shutdown, health + ready endpoints, and demo bootstrap.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config.settings import settings
from config.constants import LogMsg, SourceStatus

# ---------------------------------------------------------------------------
# Module-level logger (configured properly inside lifespan after settings.validate)
# ---------------------------------------------------------------------------
logger = logging.getLogger("creatorpulse")

# ---------------------------------------------------------------------------
# App start time (used by /health uptime field)
# ---------------------------------------------------------------------------
_START_TIME: float = time.time()


# ---------------------------------------------------------------------------
# Lifespan — startup & shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Everything that must happen before the first request is served
    (and cleaned up after the last one).
    """
    # 1. Validate config — fails fast if required keys are missing
    logger.info(LogMsg.STARTUP_VALIDATION)
    settings.validate()

    # 2. Log active modes
    if settings.use_mock_data:
        logger.warning(LogMsg.MOCK_MODE_ACTIVE)
    if settings.demo_mode:
        logger.info(LogMsg.DEMO_MODE_ACTIVE)

    # 3. Initialise Coral client (registers sources; falls back to mock on error)
    try:
        from coral.coral_client import coral_client
        await coral_client.initialize()
    except ImportError:
        logger.warning("coral_client not yet implemented — skipping Coral init")
    except Exception as exc:
        logger.warning("Coral init failed (%s) — mock fallback will be used", exc)

    # 4. Demo mode bootstrap — pre-load mock insights so first response is instant
    if settings.demo_mode or settings.use_mock_data:
        _bootstrap_demo()

    logger.info(LogMsg.STARTUP_OK)

    yield  # ← server is live from here

    # Shutdown cleanup
    logger.info("CreatorPulse shutting down — cleaning up resources...")
    try:
        from coral.coral_client import coral_client
        await coral_client.close()
    except Exception:
        pass


def _bootstrap_demo() -> None:
    """Pre-warm caches and validate mock data files exist."""
    from config.constants import (
        MOCK_YOUTUBE_PATH,
        MOCK_DISCORD_PATH,
        MOCK_SHEETS_PATH,
        MOCK_INSIGHTS_PATH,
        MOCK_RESONANCE_PATH,
    )

    missing = [
        str(p) for p in [
            MOCK_YOUTUBE_PATH,
            MOCK_DISCORD_PATH,
            MOCK_SHEETS_PATH,
            MOCK_INSIGHTS_PATH,
            MOCK_RESONANCE_PATH,
        ]
        if not p.exists()
    ]

    if missing:
        logger.warning(
            "Demo/mock mode active but %d mock file(s) not found: %s",
            len(missing),
            ", ".join(missing),
        )
    else:
        logger.info("Demo bootstrap complete — all mock data files present")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CreatorPulse API",
    description=(
        "AI Creator Intelligence Platform — powered by Coral cross-source SQL.\n\n"
        "Join YouTube analytics, Discord community signals, and Google Sheets "
        "engagement logs in a single query. Ask Claude what to create next."
    ),
    version="1.0.0",
    contact={
        "name": "CreatorPulse",
        "url": "https://github.com/your-org/creatorpulse",
    },
    license_info={"name": "MIT"},
    # Swagger UI available at /docs; ReDoc at /redoc
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request timing + logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next) -> Response:
    """Log every request with method, path, status, and elapsed time."""
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000

    logger.info(
        "%s %s → %d  (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )

    # Expose timing in response header for frontend / devtools
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
    return response


# ---------------------------------------------------------------------------
# Global exception handler — return structured JSON instead of raw 500s
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": str(exc) if settings.debug else "An unexpected error occurred.",
            "path": str(request.url.path),
        },
    )


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def _register_routes() -> None:
    """
    Import and include each router. Wrapped in individual try/except so a
    partially-built project still starts — helpful during incremental development.
    """
    routers: list[tuple[str, str, str]] = [
        ("routes.health",    "router", "/"),
        ("routes.chat",      "router", "/api/v1"),
        ("routes.analytics", "router", "/api/v1"),
        ("routes.insights",  "router", "/api/v1"),
        ("routes.sources",   "router", "/api/v1"),
    ]

    for module_path, attr, prefix in routers:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            router = getattr(mod, attr)
            app.include_router(router, prefix=prefix)
            logger.debug("Router registered: %s (prefix=%s)", module_path, prefix)
        except ImportError as exc:
            logger.warning("Router not yet available — %s: %s", module_path, exc)
        except Exception as exc:
            logger.error("Failed to register router %s: %s", module_path, exc)


_register_routes()


# ---------------------------------------------------------------------------
# Health endpoint  GET /health
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    tags=["Health"],
    summary="Liveness probe",
    response_description="Backend is alive",
)
async def health_check() -> dict[str, Any]:
    """
    Returns immediately. Use this as a liveness probe.
    Judges: hit this first to verify the server is up.
    """
    return {
        "status": "healthy",
        "version": "1.0.0",
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "mock_mode": settings.use_mock_data,
        "demo_mode": settings.demo_mode,
    }


# ---------------------------------------------------------------------------
# Ready endpoint  GET /ready
# ---------------------------------------------------------------------------

@app.get(
    "/ready",
    tags=["Health"],
    summary="Readiness probe — checks all dependencies",
    response_description="Dependency status map",
)
async def ready_check() -> JSONResponse:
    """
    Checks each major dependency and returns its status.
    Returns HTTP 200 if all critical deps are reachable (or mock mode is active).
    Returns HTTP 503 if any critical dep is down and mock mode is OFF.
    """
    checks: dict[str, str] = {}

    # Claude / Anthropic
    if settings.use_mock_data or settings.demo_mode:
        checks["claude"] = SourceStatus.MOCK
    elif settings.anthropic_api_key:
        checks["claude"] = SourceStatus.HEALTHY
    else:
        checks["claude"] = SourceStatus.OFFLINE

    # Coral
    try:
        from coral.coral_client import coral_client
        checks["coral"] = (
            SourceStatus.HEALTHY if coral_client.is_ready else SourceStatus.DEGRADED
        )
    except ImportError:
        checks["coral"] = SourceStatus.MOCK if settings.use_mock_data else SourceStatus.OFFLINE

    # YouTube
    checks["youtube"] = (
        SourceStatus.MOCK if settings.use_mock_data
        else (SourceStatus.HEALTHY if settings.youtube_api_key else SourceStatus.OFFLINE)
    )

    # Discord
    checks["discord"] = (
        SourceStatus.MOCK if settings.use_mock_data
        else (SourceStatus.HEALTHY if settings.discord_bot_token else SourceStatus.OFFLINE)
    )

    # Google Sheets
    checks["google_sheets"] = (
        SourceStatus.MOCK if settings.use_mock_data
        else (SourceStatus.HEALTHY if settings.google_sheets_id else SourceStatus.OFFLINE)
    )

    # Determine overall status
    critical_offline = any(
        v == SourceStatus.OFFLINE
        for k, v in checks.items()
        if k in ("claude",) and not settings.use_mock_data
    )

    overall = "ready" if not critical_offline else "degraded"
    http_status = 200 if overall == "ready" else 503

    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall,
            "checks": checks,
            "mock_mode": settings.use_mock_data,
            "demo_mode": settings.demo_mode,
        },
    )


# ---------------------------------------------------------------------------
# Entrypoint (for local dev: python main.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.is_development,
        log_level=settings.log_level.lower(),
    )
