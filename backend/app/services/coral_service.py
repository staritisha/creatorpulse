"""
services/coral_service.py
CreatorPulse · Coral SQL Bridge

Role: Sync-friendly wrapper around the async CoralClient.
      Every route and insight_engine import lands here.
      Handles the async→sync boundary so routes/insight_engine
      (which are sync) can call Coral without touching asyncio directly.

Used by:
  ai/insight_engine.py          — _fetch_analytics()
  routes/analytics.py           — _fetch_resonance_rows() etc.
  routes/health.py              — ping()
  routes/sources.py             — ping()

Design:
  - All public functions are synchronous (called from sync contexts).
  - They use asyncio.get_event_loop().run_until_complete() with a
    guard that works whether or not an event loop is already running.
  - When USE_MOCK_DATA=true the coral_client returns mock data
    automatically — no special handling needed here.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path to the SQL query files
_QUERIES_DIR = Path(__file__).resolve().parent.parent / "coral" / "queries"


# ---------------------------------------------------------------------------
# Async runner helper
# ---------------------------------------------------------------------------

def _run(coro) -> Any:
    """
    Run an async coroutine from sync code safely.

    - If there is NO running event loop (normal case when called from a
      sync function in a thread): create a new loop and run to completion.
    - If there IS a running loop (e.g. inside pytest-asyncio or a Jupyter
      notebook): use asyncio.run_coroutine_threadsafe via a thread executor
      so we don't nest event loops (which raises RuntimeError).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None or not loop.is_running():
        return asyncio.run(coro)

    # Running inside an existing event loop — offload to a thread
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=30)


# ---------------------------------------------------------------------------
# SQL file loader
# ---------------------------------------------------------------------------

def _load_sql(filename: str) -> str:
    """Load a .sql file from coral/queries/. Raises FileNotFoundError if missing."""
    path = _QUERIES_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"SQL file not found: {path}. "
            "Check that coral/queries/ directory is present."
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Core query functions
# ---------------------------------------------------------------------------

def query_resonance(
    channel_id: str = "demo",
    timeframe_days: int = 30,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """
    Run resonance.sql — returns per-video resonance scores.
    Used by: insight_engine._fetch_analytics(), analytics._fetch_resonance_rows()
    """
    from coral.coral_client import coral_client  # type: ignore[import]

    try:
        sql = _load_sql("resonance.sql")
    except FileNotFoundError as exc:
        logger.warning("query_resonance: %s — returning empty list", exc)
        return []

    params = {
        "timeframe_days": timeframe_days,
        "top_n": top_n,
        "topic_filter": None,
    }

    result = _run(coral_client.run_query(sql, params=params))

    if not result.success:
        logger.warning(
            "query_resonance: Coral query failed (%s) — source=%s",
            result.error, result.source,
        )
        return []

    logger.debug(
        "query_resonance: %d rows from %s in %.0fms",
        result.row_count, result.source, result.execution_ms,
    )
    return result.data


def query_trends(
    channel_id: str = "demo",
    timeframe_days: int = 90,
    bucket: str = "week",
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """
    Run trends.sql — returns rising/declining topic trends over time.
    Used by: insight_engine._fetch_analytics(), analytics._fetch_trend_rows()
    """
    from coral.coral_client import coral_client  # type: ignore[import]

    try:
        sql = _load_sql("trends.sql")
    except FileNotFoundError as exc:
        logger.warning("query_trends: %s — returning empty list", exc)
        return []

    params = {
        "timeframe_days": timeframe_days,
        "bucket": bucket,
        "top_n": top_n,
        "topic_filter": None,
    }

    result = _run(coral_client.run_query(sql, params=params))

    if not result.success:
        logger.warning(
            "query_trends: Coral query failed (%s) — source=%s",
            result.error, result.source,
        )
        return []

    logger.debug(
        "query_trends: %d rows from %s in %.0fms",
        result.row_count, result.source, result.execution_ms,
    )
    return result.data


def query_underperformers(
    channel_id: str = "demo",
    timeframe_days: int = 30,
    watch_pct_threshold: float = 40.0,
    engagement_threshold: float = 0.02,
    discord_floor: int = 5,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """
    Run underperformers.sql — returns videos that flopped + root-cause diagnosis.
    Used by: insight_engine._fetch_analytics(), analytics._fetch_underperformer_rows()
    """
    from coral.coral_client import coral_client  # type: ignore[import]

    try:
        sql = _load_sql("underperformers.sql")
    except FileNotFoundError as exc:
        logger.warning("query_underperformers: %s — returning empty list", exc)
        return []

    params = {
        "timeframe_days": timeframe_days,
        "watch_pct_threshold": watch_pct_threshold,
        "engagement_threshold": engagement_threshold,
        "discord_floor": discord_floor,
        "top_n": top_n,
    }

    result = _run(coral_client.run_query(sql, params=params))

    if not result.success:
        logger.warning(
            "query_underperformers: Coral query failed (%s) — source=%s",
            result.error, result.source,
        )
        return []

    logger.debug(
        "query_underperformers: %d rows from %s in %.0fms",
        result.row_count, result.source, result.execution_ms,
    )
    return result.data


def query_engagement(
    channel_id: str = "demo",
    timeframe_days: int = 30,
) -> list[dict[str, Any]]:
    """
    Run engagement.sql — the master 3-source JOIN.
    Used as a full data dump for deep analysis or the demo Coral reveal.
    """
    from coral.coral_client import coral_client  # type: ignore[import]

    try:
        sql = _load_sql("engagement.sql")
    except FileNotFoundError as exc:
        logger.warning("query_engagement: %s — returning empty list", exc)
        return []

    params = {"timeframe_days": timeframe_days}

    result = _run(coral_client.run_query(sql, params=params))

    if not result.success:
        logger.warning(
            "query_engagement: Coral query failed (%s) — source=%s",
            result.error, result.source,
        )
        return []

    logger.debug(
        "query_engagement: %d rows from %s in %.0fms",
        result.row_count, result.source, result.execution_ms,
    )
    return result.data


# ---------------------------------------------------------------------------
# Health check — used by routes/health.py and routes/sources.py
# ---------------------------------------------------------------------------

def ping() -> tuple[str, float | None, str]:
    """
    Check Coral client status.

    Returns:
        (status_string, latency_ms_or_None, message)

    status_string is one of:
        "healthy"   — Coral is connected and responding
        "mock"      — Running in mock/demo mode
        "degraded"  — Connected but returning errors
        "offline"   — Not reachable
    """
    from coral.coral_client import coral_client  # type: ignore[import]

    try:
        status = _run(coral_client.health())

        if status in ("mock", "healthy"):
            return status, None, f"Coral status: {status}"

        # Try a lightweight probe query to measure latency
        import time
        t0 = time.perf_counter()
        result = _run(coral_client.run_query("SELECT 1 AS probe", use_cache=False))
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)

        if result.success:
            return "healthy", latency_ms, f"Coral probe OK in {latency_ms}ms"
        else:
            return "degraded", latency_ms, f"Coral probe failed: {result.error}"

    except Exception as exc:
        logger.warning("coral_service.ping: error — %s", exc)
        return "offline", None, str(exc)


# ---------------------------------------------------------------------------
# Schema helper — used by routes/sources.py for the source status panel
# ---------------------------------------------------------------------------

def get_schema() -> dict[str, Any]:
    """Return the cached Coral schema dict (tables + columns)."""
    from coral.coral_client import coral_client  # type: ignore[import]
    try:
        return _run(coral_client.get_schema())
    except Exception as exc:
        logger.warning("coral_service.get_schema: error — %s", exc)
        return {"tables": [], "error": str(exc)}
