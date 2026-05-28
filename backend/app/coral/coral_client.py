"""
coral/coral_client.py — CreatorPulse Intelligence Backbone
Converts YouTube, Discord, and Google Sheets into one SQL-queryable layer
via the Coral CLI.  Powers every cross-source JOIN in CreatorPulse.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from config.constants import (
    CACHE_DIR,
    CORAL_QUERY_TIMEOUT,
    CORAL_SCHEMA_CACHE_TTL_HOURS,
    CORAL_RETRY_LIMIT,
    CORAL_TABLE_DISCORD,
    CORAL_TABLE_SHEETS,
    CORAL_TABLE_YOUTUBE,
    DEFAULT_TIMEFRAME_DAYS,
    MAX_QUERY_ROWS,
    LogMsg,
    SourceStatus,
)
from config.settings import settings
from services.cache_service import cache, CacheNS
logger = logging.getLogger(__name__)
# Paths
_QUERIES_DIR   = Path(__file__).parent / "queries"
_SCHEMA_CACHE  = CACHE_DIR / "coral_schema.json"
# SQL keywords that are never allowed (12. safety)
_BLOCKED_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|REPLACE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)
# ---------------------------------------------------------------------------
# 8. Normalised query result
# ---------------------------------------------------------------------------
@dataclass
class QueryResult:
    success:        bool
    data:           list[dict[str, Any]] = field(default_factory=list)
    row_count:      int = 0
    execution_ms:   float = 0.0
    sql:            str = ""
    error:          Optional[str] = None
    source:         str = "coral"          # "coral" | "mock"
    schema_used:    list[str] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]:
        return {
            "success":      self.success,
            "row_count":    self.row_count,
            "execution_ms": round(self.execution_ms, 2),
            "source":       self.source,
            "data":         self.data,
            "error":        self.error,
        }
# ---------------------------------------------------------------------------
# Coral Client
# ---------------------------------------------------------------------------
class CoralClient:
    """
    Async wrapper around the Coral CLI.
    Registers YouTube, Discord, and Google Sheets sources on startup,
    executes SQL queries, caches results, and falls back to mock data
    automatically when USE_MOCK_DATA=true or Coral is unavailable.
    """
    def __init__(self) -> None:
        self._coral_bin:   str                     = settings.coral_path
        self._is_ready:    bool                    = False
        self._status:      str                     = SourceStatus.OFFLINE
        self._schema:      Optional[dict[str, Any]] = None
        self._query_times: list[float]             = []   # 17. perf tracking
    # ------------------------------------------------------------------
    # 2. Readiness state
    # ------------------------------------------------------------------
    @property
    def is_ready(self) -> bool:
        return self._is_ready
    async def health(self) -> str:
        if settings.use_mock_data:
            return SourceStatus.MOCK
        return self._status
    # ------------------------------------------------------------------
    # 1. Initialization  (called from main.py lifespan)
    # ------------------------------------------------------------------
async def initialize(self) -> None:
    """
        Verify Coral is installed, register all sources, load schema.
        Falls back gracefully — never raises so the server always starts.
        """
        if settings.use_mock_data:
            logger.info(LogMsg.MOCK_MODE_ACTIVE)
            self._is_ready = True
            self._status   = SourceStatus.MOCK
            return
        # 1a. Check Coral binary exists
        if not await self._coral_available():
            logger.warning(
                "Coral binary '%s' not found — switching to mock mode", self._coral_bin
            )
            self._status   = SourceStatus.OFFLINE
            self._is_ready = True   # still mark ready so server starts
            return
        # 3. Register sources
        await self._register_sources()
        # 13 & 14. Load + cache schema
        await self._load_schema()
        self._is_ready = True
        self._status   = SourceStatus.HEALTHY
        logger.info(LogMsg.STARTUP_OK + " — Coral ready")
    async def _coral_available(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._coral_bin, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5)
            return proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError):
            return False
    # ------------------------------------------------------------------
    # 3. Source Registration
    # ------------------------------------------------------------------
    async def _register_sources(self) -> None:
        sources = [
            {
                "name":   "youtube",
                "type":   "http",
                "config": {
                    "base_url": "https://www.googleapis.com/youtube/v3",
                    "auth":     {"type": "api_key", "key": settings.youtube_api_key or ""},
                },
            },
            {
                "name":   "discord",
                "type":   "http",
                "config": {
                    "base_url": "https://discord.com/api/v10",
                    "auth":     {"type": "bearer", "token": settings.discord_bot_token or ""},
                },
            },
            {
                "name":   "gsheets",
                "type":   "google_sheets",
                "config": {
                    "sheet_id":              settings.google_sheets_id or "",
                    "service_account_path":  settings.google_service_account_path,
                },
            },
        ]
        for src in sources:
            try:
                await self._run_coral_cmd([
                    "source", "add",
                    "--name",   src["name"],
                    "--type",   src["type"],
                    "--config", json.dumps(src["config"]),
                ])
                logger.debug("Coral source registered: %s", src["name"])
            except Exception as exc:
                logger.warning("Failed to register Coral source '%s': %s", src["name"], exc)
    # ------------------------------------------------------------------
    # 13 & 14. Schema Discovery + Caching
    # ------------------------------------------------------------------
    async def _load_schema(self) -> None:
        # Try disk cache first
        if _SCHEMA_CACHE.exists():
            try:
                cached = json.loads(_SCHEMA_CACHE.read_text())
                age_hours = (time.time() - cached.get("_ts", 0)) / 3600
                if age_hours < CORAL_SCHEMA_CACHE_TTL_HOURS:
                    self._schema = cached
                    logger.info(LogMsg.CORAL_SCHEMA_CACHED)
                    return
            except Exception:
                pass
        logger.info(LogMsg.CORAL_SCHEMA_REFRESH)
        try:
            raw = await self._run_coral_cmd(["schema", "--json"])
            schema = json.loads(raw)
            schema["_ts"] = time.time()
            self._schema = schema
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _SCHEMA_CACHE.write_text(json.dumps(schema, indent=2))
        except Exception as exc:
            logger.warning("Schema discovery failed: %s", exc)
            self._schema = {"tables": [], "_ts": time.time()}
    async def get_schema(self) -> dict[str, Any]:
        if self._schema is None:
            await self._load_schema()
        return self._schema or {}
    # ------------------------------------------------------------------
    # 4 & 12. SQL Query Execution with safety validation
    # ------------------------------------------------------------------
    async def run_query(
        self,
        sql: str,
        params: Optional[dict[str, Any]] = None,
        use_cache: bool = True,
    ) -> QueryResult:
        """
        Execute a Coral SQL query safely.
        1. Validate SQL (block dangerous keywords)
        2. Check cache
        3. Execute via Coral CLI with timeout + retry
        4. Normalise result
        5. Store in cache
        """
        # 12. Safety validation
        blocked = _BLOCKED_KEYWORDS.search(sql)
        if blocked:
            return QueryResult(
                success=False,
                sql=sql,
                error=f"Blocked SQL keyword: {blocked.group()}. Only SELECT queries allowed.",
            )
        # 9. Cache lookup
        if use_cache and settings.coral_cache_enabled:
            cached = await cache.get_coral(sql, params)
            if cached is not None:
                return cached
        # 15. Mock fallback
        if settings.use_mock_data or not self._is_ready or self._status == SourceStatus.OFFLINE:
            return await self._mock_query_result(sql)
        # 4. Execute with retry
        start = time.perf_counter()
        result = await self._execute_with_retry(sql, params)
        elapsed_ms = (time.perf_counter() - start) * 1000
        result.execution_ms = elapsed_ms
        # 17. Performance tracking
        self._query_times.append(elapsed_ms)
        if len(self._query_times) > 200:
            self._query_times = self._query_times[-200:]
        if elapsed_ms > 10_000:
            logger.warning("SLOW Coral query (%.0fms): %.80s", elapsed_ms, sql.strip())
        # 9. Cache the result
        if result.success and use_cache and settings.coral_cache_enabled:
            await cache.set_coral(sql, params, result)
        return result
    async def _execute_with_retry(
        self,
        sql: str,
        params: Optional[dict[str, Any]],
    ) -> QueryResult:
        """11. Retry with back-off on transient failures."""
        last_error: Optional[str] = None
        for attempt in range(1, CORAL_RETRY_LIMIT + 2):
            try:
                raw = await self._run_coral_query(sql, params)
                rows = self._parse_rows(raw)
                logger.info(
                    LogMsg.CORAL_QUERY_OK,
                    (time.perf_counter()),
                    len(rows),
                )
                return QueryResult(
                    success   = True,
                    data      = rows[:MAX_QUERY_ROWS],
                    row_count = len(rows),
                    sql       = sql,
                    source    = "coral",
                )
            except asyncio.TimeoutError:
                last_error = f"Query timed out after {CORAL_QUERY_TIMEOUT}s"
                logger.warning(LogMsg.CORAL_QUERY_TIMEOUT, CORAL_QUERY_TIMEOUT)
                break
            except Exception as exc:
                last_error = str(exc)
                if attempt <= CORAL_RETRY_LIMIT:
                    wait = 1.5 * attempt
                    logger.warning("Coral attempt %d failed (%s) — retry in %.1fs", attempt, exc, wait)
                    await asyncio.sleep(wait)
        # All retries exhausted → mock fallback
        logger.warning(LogMsg.CORAL_FALLBACK_MOCK + " — %s", last_error)
        self._status = SourceStatus.DEGRADED
        result = await self._mock_query_result(sql)
        result.error = last_error
        return result
    # ------------------------------------------------------------------
    # Coral CLI subprocess helpers
    # --------------------------------------------------...
[truncated]