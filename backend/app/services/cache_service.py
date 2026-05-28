"""
services/cache_service.py — CreatorPulse Performance Layer

In-memory TTL cache for Coral query results, Claude insights, resonance
scores, and analytics summaries.  No Redis required — uses cachetools.
All public methods are async-safe via asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from cachetools import TTLCache

from config.constants import (
    CACHE_MAX_SIZE,
    CACHE_TTL,
    HACKATHON_CACHE_TTL,
    MOCK_INSIGHTS_PATH,
    MOCK_RESONANCE_PATH,
    MOCK_YOUTUBE_PATH,
    MOCK_DISCORD_PATH,
    MOCK_SHEETS_PATH,
)
from config.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache namespace prefixes — prevents key collisions across data types
# ---------------------------------------------------------------------------

class CacheNS(str, Enum):
    """Cache namespace prefixes — one per data category."""
    CORAL    = "coral"      # Coral SQL query results
    INSIGHT  = "insight"    # Claude-generated insights
    RESONANCE = "resonance" # Per-video resonance scores
    ANALYTICS = "analytics" # Dashboard summaries
    MOCK     = "mock"       # Mock JSON payloads (loaded once)
    GENERIC  = "generic"    # Any other ad-hoc values


# ---------------------------------------------------------------------------
# Internal per-namespace TTLCaches
# ---------------------------------------------------------------------------

def _make_cache(ttl: int, maxsize: int) -> TTLCache:
    return TTLCache(maxsize=maxsize, ttl=ttl)


class _CacheStore:
    """Holds one TTLCache per namespace with independent TTLs."""

    def __init__(self) -> None:
        effective_ttl = (
            HACKATHON_CACHE_TTL if settings.is_hackathon else CACHE_TTL
        )

        self._stores: dict[CacheNS, TTLCache] = {
            CacheNS.CORAL:     _make_cache(ttl=effective_ttl,    maxsize=CACHE_MAX_SIZE),
            CacheNS.INSIGHT:   _make_cache(ttl=effective_ttl * 2, maxsize=64),  # insights stay longer
            CacheNS.RESONANCE: _make_cache(ttl=effective_ttl,    maxsize=CACHE_MAX_SIZE),
            CacheNS.ANALYTICS: _make_cache(ttl=effective_ttl,    maxsize=32),
            CacheNS.MOCK:      _make_cache(ttl=3600 * 24,        maxsize=32),   # mocks survive the session
            CacheNS.GENERIC:   _make_cache(ttl=effective_ttl,    maxsize=128),
        }

        # Stats counters — never reset during the process lifetime
        self._hits: dict[CacheNS, int] = {ns: 0 for ns in CacheNS}
        self._misses: dict[CacheNS, int] = {ns: 0 for ns in CacheNS}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _store(self, ns: CacheNS) -> TTLCache:
        return self._stores[ns]

    # ------------------------------------------------------------------
    # Core get / set / delete
    # ------------------------------------------------------------------

    def get(self, ns: CacheNS, key: str) -> Optional[Any]:
        value = self._store(ns).get(key)
        if value is not None:
            self._hits[ns] += 1
        else:
            self._misses[ns] += 1
        return value

    def set(self, ns: CacheNS, key: str, value: Any) -> None:
        self._store(ns)[key] = value

    def delete(self, ns: CacheNS, key: str) -> None:
        self._store(ns).pop(key, None)

    # ------------------------------------------------------------------
    # Invalidation helpers
    # ------------------------------------------------------------------

    def clear_namespace(self, ns: CacheNS) -> int:
        """Clear all entries in one namespace. Returns number cleared."""
        store = self._store(ns)
        count = len(store)
        store.clear()
        return count

    def clear_all(self) -> int:
        """Nuke every namespace. Returns total entries cleared."""
        total = sum(len(s) for s in self._stores.values())
        for store in self._stores.values():
            store.clear()
        return total

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        rows = []
        for ns in CacheNS:
            hits   = self._hits[ns]
            misses = self._misses[ns]
            total  = hits + misses
            rows.append({
                "namespace": ns.value,
                "size":      len(self._store(ns)),
                "capacity":  self._store(ns).maxsize,
                "hits":      hits,
                "misses":    misses,
                "hit_rate":  round(hits / total, 3) if total else None,
            })
        return {"namespaces": rows, "total_entries": sum(len(s) for s in self._stores.values())}


# ---------------------------------------------------------------------------
# Public CacheService  (singleton, async-safe)
# ---------------------------------------------------------------------------

class CacheService:
    """
    Async-safe cache service.  All mutating methods acquire a per-namespace lock.

    Typical usage:
        from services.cache_service import cache

        result = await cache.get_coral(sql, params)
        if result is None:
            result = await run_expensive_query()
            await cache.set_coral(sql, params, result)
    """

    def __init__(self) -> None:
        self._store = _CacheStore()
        # One lock per namespace — maximises concurrency without races
        self._locks: dict[CacheNS, asyncio.Lock] = {ns: asyncio.Lock() for ns in CacheNS}

    # ------------------------------------------------------------------
    # 7. Cache Key Generation
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(*parts: Any) -> str:
        """
        Deterministic cache key from arbitrary parts.
        Serialises to JSON then SHA-256 so complex dicts/lists are safe.
        """
        raw = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Generic get / set / delete (async-safe)
    # ------------------------------------------------------------------

    async def get(self, ns: CacheNS, key: str) -> Optional[Any]:
        async with self._locks[ns]:
            return self._store.get(ns, key)

    async def set(self, ns: CacheNS, key: str, value: Any) -> None:
        async with self._locks[ns]:
            self._store.set(ns, key, value)

    async def delete(self, ns: CacheNS, key: str) -> None:
        async with self._locks[ns]:
            self._store.delete(ns, key)

    # ------------------------------------------------------------------
    # 2. Coral Query Result Caching
    # ------------------------------------------------------------------

    async def get_coral(self, sql: str, params: Optional[dict] = None) -> Optional[Any]:
        key = self.make_key(CacheNS.CORAL, sql, params or {})
        result = await self.get(CacheNS.CORAL, key)
        if result is not None:
            logger.debug("Cache HIT [coral] %.40s...", sql.strip())
        return result

    async def set_coral(self, sql: str, params: Optional[dict], value: Any) -> None:
        key = self.make_key(CacheNS.CORAL, sql, params or {})
        await self.set(CacheNS.CORAL, key, value)
        logger.debug("Cache SET [coral] %.40s...", sql.strip())

    # ------------------------------------------------------------------
    # 3. AI Insight Response Caching
    # ------------------------------------------------------------------

    async def get_insight(self, question: str, context_hash: str) -> Optional[Any]:
        key = self.make_key(CacheNS.INSIGHT, question, context_hash)
        result = await self.get(CacheNS.INSIGHT, key)
        if result is not None:
            logger.debug("Cache HIT [insight] %.50s...", question)
        return result

    async def set_insight(self, question: str, context_hash: str, value: Any) -> None:
        key = self.make_key(CacheNS.INSIGHT, question, context_hash)
        await self.set(CacheNS.INSIGHT, key, value)
        logger.debug("Cache SET [insight] %.50s...", question)

    # ------------------------------------------------------------------
    # 4. Resonance Score Caching
    # ------------------------------------------------------------------

    async def get_resonance(self, video_id: str) -> Optional[float]:
        key = self.make_key(CacheNS.RESONANCE, video_id)
        return await self.get(CacheNS.RESONANCE, key)

    async def set_resonance(self, video_id: str, score: float) -> None:
        key = self.make_key(CacheNS.RESONANCE, video_id)
        await self.set(CacheNS.RESONANCE, key, score)

    async def get_resonance_bulk(self, video_ids: list[str]) -> dict[str, Optional[float]]:
        """Return {video_id: score_or_None} for a batch of IDs in one lock acquire."""
        async with self._locks[CacheNS.RESONANCE]:
            return {
                vid: self._store.get(CacheNS.RESONANCE, self.make_key(CacheNS.RESONANCE, vid))
                for vid in video_ids
            }

    # ------------------------------------------------------------------
    # 5. Analytics Dashboard Caching
    # ------------------------------------------------------------------

    async def get_analytics(self, endpoint: str, timeframe: str) -> Optional[Any]:
        key = self.make_key(CacheNS.ANALYTICS, endpoint, timeframe)
        result = await self.get(CacheNS.ANALYTICS, key)
        if result is not None:
            logger.debug("Cache HIT [analytics] %s/%s", endpoint, timeframe)
        return result

    async def set_analytics(self, endpoint: str, timeframe: str, value: Any) -> None:
        key = self.make_key(CacheNS.ANALYTICS, endpoint, timeframe)
        await self.set(CacheNS.ANALYTICS, key, value)

    # ------------------------------------------------------------------
    # 14. Mock Data Cache Support
    # ------------------------------------------------------------------

    async def get_mock(self, source: str) -> Optional[Any]:
        """Return mock JSON for a source, loading from disk once if needed."""
        async with self._locks[CacheNS.MOCK]:
            cached = self._store.get(CacheNS.MOCK, source)
            if cached is not None:
                return cached

            path_map: dict[str, Path] = {
                "youtube":  MOCK_YOUTUBE_PATH,
                "discord":  MOCK_DISCORD_PATH,
                "sheets":   MOCK_SHEETS_PATH,
                "insights": MOCK_INSIGHTS_PATH,
                "resonance": MOCK_RESONANCE_PATH,
            }

            path = path_map.get(source)
            if path is None or not path.exists():
                logger.warning("Mock file not found for source '%s' at %s", source, path)
                return None

            data = json.loads(path.read_text(encoding="utf-8"))
            self._store.set(CacheNS.MOCK, source, data)
            logger.debug("Mock data loaded and cached for source '%s'", source)
            return data

    # ------------------------------------------------------------------
    # 10 & 11. Cache Invalidation — full and selective
    # ------------------------------------------------------------------

    async def invalidate(self, ns: CacheNS, key: str) -> None:
        """Remove a single entry."""
        await self.delete(ns, key)

    async def clear_namespace(self, ns: CacheNS) -> int:
        """Clear all entries in one namespace."""
        async with self._locks[ns]:
            count = self._store.clear_namespace(ns)
        logger.info("Cache cleared [%s] — %d entries removed", ns.value, count)
        return count

    async def clear_all(self) -> int:
        """Clear every namespace. Useful during development / test resets."""
        for lock in self._locks.values():
            await lock.acquire()
        try:
            total = self._store.clear_all()
        finally:
            for lock in self._locks.values():
                lock.release()
        logger.info("Full cache cleared — %d total entries removed", total)
        return total

    # ------------------------------------------------------------------
    # 12. Cache Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return per-namespace hit/miss rates and memory usage."""
        return self._store.stats()

    # ------------------------------------------------------------------
    # 13. Demo Mode Cache Pre-warming
    # ------------------------------------------------------------------

    async def prewarm(self) -> None:
        """
        Pre-load mock data into cache at startup so the very first demo
        request feels instant.  Called by main.py _bootstrap_demo().
        """
        if not (settings.use_mock_data or settings.demo_mode):
            return

        logger.info("Pre-warming cache for demo/mock mode...")
        t0 = time.perf_counter()

        for source in ("youtube", "discord", "sheets", "insights", "resonance"):
            await self.get_mock(source)

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("Cache pre-warm complete in %.1fms", elapsed)

    # ------------------------------------------------------------------
    # Convenience: context-hash helper for insight caching
    # ------------------------------------------------------------------

    @staticmethod
    def hash_context(data: Any) -> str:
        """Produce a short hash of a data context (DataFrame, dict, list)."""
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Singleton — import this everywhere
# ---------------------------------------------------------------------------
cache = CacheService()
