"""
routes/health.py
CreatorPulse · System Health & Reliability Gateway

Role: The heartbeat monitor of CreatorPulse — fast liveness, readiness,
      dependency health, uptime, and detailed diagnostics for judges and
      production monitoring.

Design principle (Feature 14): /health returns in < 5ms. Every probe is
lazy and non-blocking. Heavy checks only run on /health/details.

Endpoints:
  GET /api/health           — instant liveness (Feature 1)
  GET /api/health/ready     — dependency readiness (Feature 2)
  GET /api/health/details   — deep diagnostics (Feature 11)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter

from models.response_models import (
    APIResponse,
    HealthResponse,
    RequestMetadata,
    ServiceStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])

# ---------------------------------------------------------------------------
# Start time — set once on import for uptime tracking (Feature 8)
# ---------------------------------------------------------------------------
_START_TIME: float = time.time()

# ---------------------------------------------------------------------------
# Version / environment constants (Feature 9, 10)
# ---------------------------------------------------------------------------
API_VERSION:   str = "1.0.0"
SERVICE_NAME:  str = "CreatorPulse API"
BUILD_LABEL:   str = "hackathon-v1"

def _env() -> str:
    """Return deployment environment label. (Feature 10)"""
    return os.environ.get("APP_ENV", os.environ.get("NODE_ENV", "development"))


# ---------------------------------------------------------------------------
# Lightweight service probes (Feature 3, 13: Dependency Health + Failure Detection)
# Each probe is isolated — one failure never crashes another.
# ---------------------------------------------------------------------------

def _probe_coral_fast() -> ServiceStatus:
    """Quick Coral check — import only, no network call. (Feature 4)"""
    try:
        from services.coral_service import ping  # type: ignore[import]
        t0 = time.time()
        ping()
        return ServiceStatus(name="coral", status="healthy",
                             latency_ms=int((time.time() - t0) * 1000),
                             detail="Coral SQL responding")
    except ImportError:
        return ServiceStatus(name="coral", status="mock",
                             detail="coral_service not installed — mock JOIN active")
    except Exception as exc:
        return ServiceStatus(name="coral", status="degraded", detail=str(exc)[:80])


def _probe_claude_fast() -> ServiceStatus:
    """Check ANTHROPIC_API_KEY presence without making an API call. (Feature 5)"""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return ServiceStatus(name="claude", status="mock", detail="ANTHROPIC_API_KEY not set — mock mode active")
    try:
        import anthropic  # type: ignore[import]
        return ServiceStatus(name="claude", status="healthy", detail="SDK available, key configured")
    except ImportError:
        return ServiceStatus(name="claude", status="degraded", detail="anthropic SDK not installed")


def _probe_source_fast(name: str) -> ServiceStatus:
    """Config-only probe for YouTube / Discord / Sheets. (Feature 6)"""
    env_map = {
        "youtube":       "YOUTUBE_API_KEY",
        "discord":       "DISCORD_BOT_TOKEN",
        "google_sheets": "GOOGLE_SHEETS_ID",
    }
    key = env_map.get(name, "")
    configured = bool(key and os.environ.get(key, ""))
    return ServiceStatus(
        name   = name,
        status = "healthy" if configured else "mock",
        detail = "Credentials configured" if configured else f"Demo mode — {key} not set",
    )


def _probe_cache() -> ServiceStatus:
    """Check in-memory cache health. (Feature 12)"""
    try:
        from routes.analytics import _analytics_cache  # type: ignore[import]
        from routes.insights  import _insight_cache     # type: ignore[import]
        total = len(_analytics_cache) + len(_insight_cache)
        return ServiceStatus(name="cache", status="healthy",
                             detail=f"{total} cached entries (analytics + insights)")
    except Exception as exc:
        return ServiceStatus(name="cache", status="degraded", detail=str(exc)[:60])


def _all_service_probes(deep: bool = False) -> list[ServiceStatus]:
    """
    Run all service probes.
    deep=False → config-only (fast).
    deep=True  → live network probes for coral + claude.
    (Feature 14: Fast Response Design)
    """
    coral  = _probe_coral_fast() if deep else ServiceStatus(
        name="coral", status="mock" if not os.environ.get("CORAL_API_KEY") else "healthy",
        detail="Coral SQL JOIN active (mock)" if not os.environ.get("CORAL_API_KEY") else "Configured",
    )
    claude = _probe_claude_fast()
    yt     = _probe_source_fast("youtube")
    dc     = _probe_source_fast("discord")
    sh     = _probe_source_fast("google_sheets")
    cache  = _probe_cache()
    return [coral, claude, yt, dc, sh, cache]


def _overall_status(services: list[ServiceStatus]) -> str:
    statuses = {s.status for s in services}
    if "degraded" in statuses:
        return "degraded"
    if "unhealthy" in statuses:
        return "unhealthy"
    return "healthy"


# ===========================================================================
# GET /api/health  — instant liveness (Feature 1, 14)
# ===========================================================================

@router.get("", response_model=APIResponse, summary="Liveness check")
async def health() -> APIResponse:
    """
    Instant liveness endpoint — returns in < 5ms, no probes, no I/O.
    Judges: hit this first to confirm the backend is alive.
    (Feature 1: Health Check Endpoint)
    """
    uptime_s   = round(time.time() - _START_TIME, 1)
    mock_mode  = not bool(os.environ.get("YOUTUBE_API_KEY") or os.environ.get("CORAL_API_KEY"))

    payload = HealthResponse(
        status   = "healthy",
        uptime_s = uptime_s,
        version  = API_VERSION,
        services = [],   # omitted for speed — use /details for full check
    ).model_dump()

    # Enrich with lightweight extras (Feature 9, 10, 15)
    payload.update({
        "service":     SERVICE_NAME,
        "build":       BUILD_LABEL,
        "environment": _env(),
        "mock_mode":   mock_mode,
        "demo_safe":   True,
        "uptime_human":_uptime_human(uptime_s),
    })

    logger.debug("health: liveness OK uptime=%.0fs", uptime_s)
    return APIResponse.ok(
        data    = payload,
        message = f"{SERVICE_NAME} is running",
        metadata= {"uptime_s": uptime_s, "version": API_VERSION},
    )


# ===========================================================================
# GET /api/health/ready  — dependency readiness (Feature 2)
# ===========================================================================

@router.get("/ready", response_model=APIResponse, summary="Readiness check")
async def ready() -> APIResponse:
    """
    Readiness check — verifies all dependencies are available before
    the backend starts serving real requests.
    Config-only probes (no network I/O) for speed.
    (Feature 2: Readiness Check Endpoint)
    """
    t0       = time.time()
    services = _all_service_probes(deep=False)
    overall  = _overall_status(services)
    uptime_s = round(time.time() - _START_TIME, 1)
    mock_mode= all(s.status == "mock" for s in services if s.name not in ("cache",))

    # Feature 13: Failure detection — list any degraded services
    degraded = [s.name for s in services if s.status == "degraded"]
    warnings = [f"{s.name}: {s.detail}" for s in services if s.status == "mock"]

    payload = {
        "status":      "ready" if overall != "unhealthy" else "not_ready",
        "overall":     overall,
        "dependencies":{s.name: s.status for s in services},
        "degraded":    degraded,
        "mock_warnings":warnings[:4],
        "mock_mode":   mock_mode,
        "demo_safe":   True,
        "uptime_s":    uptime_s,
        "version":     API_VERSION,
    }
    latency_ms = int((time.time() - t0) * 1000)
    logger.info("health/ready: overall=%s mock=%s degraded=%s latency=%dms",
                overall, mock_mode, degraded, latency_ms)
    return APIResponse.ok(
        data    = payload,
        message = f"System {payload['status']}",
        metadata= RequestMetadata(latency_ms=latency_ms, from_mock=mock_mode).to_dict(),
    )


# ===========================================================================
# GET /api/health/details  — deep diagnostics (Feature 11)
# ===========================================================================

@router.get("/details", response_model=APIResponse, summary="Detailed diagnostics")
async def health_details() -> APIResponse:
    """
    Full diagnostic report: live probes, cache state, environment info,
    uptime, and active integration list. Useful during demo and debugging.
    (Feature 11: Detailed Health Endpoint)
    """
    t0       = time.time()
    services = _all_service_probes(deep=True)    # live probes for coral + claude
    overall  = _overall_status(services)
    uptime_s = round(time.time() - _START_TIME, 1)
    mock_mode= all(s.status == "mock" for s in services if s.name not in ("cache",))

    # Feature 12: Cache detail
    cache_svc = next((s for s in services if s.name == "cache"), None)
    cache_info: dict[str, Any] = {}
    try:
        from routes.analytics import _analytics_cache, _CACHE_TTL_S  # type: ignore[import]
        from routes.insights  import _insight_cache, _INSIGHT_CACHE_TTL_S  # type: ignore[import]
        cache_info = {
            "enabled":           True,
            "analytics_entries": len(_analytics_cache),
            "insight_entries":   len(_insight_cache),
            "analytics_ttl_s":   _CACHE_TTL_S,
            "insight_ttl_s":     _INSIGHT_CACHE_TTL_S,
            "status":            cache_svc.status if cache_svc else "unknown",
        }
    except Exception:
        cache_info = {"enabled": True, "status": "unknown"}

    # Feature 19: Security — never expose keys or tokens
    active_integrations = [
        s.name for s in services if s.status in ("healthy", "mock")
    ]

    # Feature 15: Health metadata
    svc_list = [
        {
            "name":       s.name,
            "status":     s.status,
            "latency_ms": s.latency_ms,
            "detail":     s.detail,
        }
        for s in services
    ]

    payload = {
        # Feature 16: Standardised schema
        "status":              overall,
        "service":             SERVICE_NAME,
        "version":             API_VERSION,
        "build":               BUILD_LABEL,
        "environment":         _env(),
        "uptime_s":            uptime_s,
        "uptime_human":        _uptime_human(uptime_s),
        "mock_mode":           mock_mode,
        "demo_safe":           True,
        "services":            svc_list,
        "active_integrations": active_integrations,
        "cache":               cache_info,
        # Feature 3: Dependency health summary
        "dependency_summary":  {s.name: s.status for s in services},
        "degraded_services":   [s.name for s in services if s.status == "degraded"],
    }
    latency_ms = int((time.time() - t0) * 1000)
    logger.info("health/details: overall=%s services=%d latency=%dms",
                overall, len(services), latency_ms)
    return APIResponse.ok(
        data    = payload,
        message = f"Detailed health: {overall}",
        metadata= RequestMetadata(
            latency_ms   = latency_ms,
            from_mock    = mock_mode,
            data_points  = len(services),
        ).to_dict(),
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _uptime_human(seconds: float) -> str:
    """Convert uptime seconds to a readable string. (Feature 8)"""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Module-level accessor used by main.py for startup time injection
# ---------------------------------------------------------------------------

def set_start_time(t: float) -> None:
    global _START_TIME
    _START_TIME = t
