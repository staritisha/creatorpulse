"""
coral/schema_cache.py
CreatorPulse · Coral Schema Intelligence Manager

Role: Discover, cache, validate, and manage Coral source schemas so the rest
      of the application knows exactly which tables, columns, and JOIN paths
      are available — without hitting Coral on every request.

Used by:
  coral/coral_client.py      — startup registration & query validation
  ai/llm_client.py           — schema injection into Claude context
  ai/insight_engine.py       — AI context enrichment
  routes/sources.py          — /sources/status health endpoint
  main.py                    — /ready readiness probe
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS: int = 6 * 3600          # 6-hour expiry (Feature 11)
CACHE_FILE: Path = Path("data/cached/coral_schema.json")

# Canonical Coral source names used across all SQL queries
KNOWN_SOURCES: list[str] = ["youtube", "discord", "sheets"]

# ---------------------------------------------------------------------------
# Static mock schemas
# Loaded when Coral is unreachable — keeps the query system alive during
# the hackathon demo if an API is down. (Feature 13)
# ---------------------------------------------------------------------------

MOCK_SCHEMAS: dict[str, dict[str, Any]] = {
    "youtube.videos": {
        "source": "youtube",
        "table": "videos",
        "columns": {
            "video_id":     {"type": "TEXT",      "nullable": False},
            "title":        {"type": "TEXT",      "nullable": False},
            "topic":        {"type": "TEXT",      "nullable": True},
            "tag":          {"type": "TEXT",      "nullable": True},
            "published_at": {"type": "TIMESTAMP", "nullable": False},
            "views":        {"type": "INTEGER",   "nullable": False},
            "likes":        {"type": "INTEGER",   "nullable": True},
            "comments":     {"type": "INTEGER",   "nullable": True},
            "ctr":          {"type": "FLOAT",     "nullable": True},
            "watch_pct":    {"type": "FLOAT",     "nullable": True},
        },
    },
    "youtube.channels": {
        "source": "youtube",
        "table": "channels",
        "columns": {
            "channel_id":   {"type": "TEXT",      "nullable": False},
            "name":         {"type": "TEXT",      "nullable": False},
            "subscribers":  {"type": "INTEGER",   "nullable": True},
            "total_views":  {"type": "INTEGER",   "nullable": True},
            "joined_at":    {"type": "TIMESTAMP", "nullable": True},
        },
    },
    "discord.messages": {
        "source": "discord",
        "table": "messages",
        "columns": {
            "message_id":   {"type": "TEXT",      "nullable": False},
            "channel_id":   {"type": "TEXT",      "nullable": False},
            "author_id":    {"type": "TEXT",      "nullable": False},
            "keyword":      {"type": "TEXT",      "nullable": True},
            "content":      {"type": "TEXT",      "nullable": True},
            "reply_count":  {"type": "INTEGER",   "nullable": True},
            "daily_count":  {"type": "INTEGER",   "nullable": True},
            "created_at":   {"type": "TIMESTAMP", "nullable": False},
        },
    },
    "discord.channels": {
        "source": "discord",
        "table": "channels",
        "columns": {
            "channel_id":   {"type": "TEXT",      "nullable": False},
            "name":         {"type": "TEXT",      "nullable": False},
            "guild_id":     {"type": "TEXT",      "nullable": False},
        },
    },
    "sheets.engagement_log": {
        "source": "sheets",
        "table": "engagement_log",
        "columns": {
            "video_id":     {"type": "TEXT",      "nullable": True},
            "video_title":  {"type": "TEXT",      "nullable": False},
            "date":         {"type": "DATE",      "nullable": False},
            "watch_pct":    {"type": "FLOAT",     "nullable": True},
            "views":        {"type": "INTEGER",   "nullable": True},
            "likes":        {"type": "INTEGER",   "nullable": True},
            "comments":     {"type": "INTEGER",   "nullable": True},
        },
    },
}

# ---------------------------------------------------------------------------
# Known JOIN relationships across sources (Feature 7)
# Each entry describes how two tables can be correlated.
# Used by insight_engine.py to validate JOIN clauses before execution.
# ---------------------------------------------------------------------------

JOIN_RELATIONSHIPS: list[dict[str, str]] = [
    {
        "left_table":  "youtube.videos",
        "right_table": "sheets.engagement_log",
        "left_key":    "video_id",
        "right_key":   "video_id",
        "fallback":    "title ≈ video_title",
        "description": "Exact video ID match; title fuzzy-match as fallback.",
    },
    {
        "left_table":  "youtube.videos",
        "right_table": "discord.messages",
        "left_key":    "topic",
        "right_key":   "keyword",
        "fallback":    "ILIKE fuzzy topic correlation",
        "description": "Topic keyword correlation — no shared ID across sources.",
    },
    {
        "left_table":  "discord.messages",
        "right_table": "sheets.engagement_log",
        "left_key":    "keyword",
        "right_key":   "video_title",
        "fallback":    "ILIKE fuzzy title match",
        "description": "Indirect join via shared video topic / title.",
    },
]


# ===========================================================================
# SchemaCache
# ===========================================================================

class SchemaCache:
    """
    In-memory + disk-backed cache for Coral source schemas.

    Lifecycle
    ---------
    1. On startup  → load() tries disk cache first, then Coral discovery.
    2. On request  → get_columns() / get_table_names() return in-memory data.
    3. After 6h    → is_stale() returns True; next load() fetches fresh data.
    4. On demand   → refresh() forces a new Coral discovery cycle.
    5. If Coral fails at any point → _load_mock() fills in stub schemas so
       SQL queries still resolve (mock mode compatibility, Feature 13).
    """

    def __init__(self) -> None:
        # The live schema registry: { "source.table": { columns, ... } }
        self._schemas: dict[str, dict[str, Any]] = {}
        self._loaded_at: float = 0.0
        self._source_status: dict[str, str] = {}   # "healthy" | "degraded" | "unavailable"
        self._using_mock: bool = False

    # ------------------------------------------------------------------
    # Public API — Feature 12: Fast Schema Lookup
    # ------------------------------------------------------------------

    def get_table_names(self) -> list[str]:
        """Return all registered Coral table names (Feature 3)."""
        return list(self._schemas.keys())

    def get_columns(self, table: str) -> dict[str, Any]:
        """
        Return column metadata for a given 'source.table' string (Feature 4).
        Returns an empty dict if the table is not in the registry.
        """
        return self._schemas.get(table, {}).get("columns", {})

    def get_column_names(self, table: str) -> list[str]:
        """Convenience: return just the column name list for a table."""
        return list(self.get_columns(table).keys())

    def get_all_schemas(self) -> dict[str, dict[str, Any]]:
        """Return the full schema registry (used by AI context enrichment)."""
        return dict(self._schemas)

    def get_join_relationships(self) -> list[dict[str, str]]:
        """Return all known cross-source JOIN paths (Feature 7)."""
        return JOIN_RELATIONSHIPS

    def source_status(self) -> dict[str, str]:
        """Return per-source health status (Feature 6, Feature 16)."""
        return dict(self._source_status)

    def is_stale(self) -> bool:
        """Return True if the cache has exceeded its TTL (Feature 11)."""
        return (time.time() - self._loaded_at) > CACHE_TTL_SECONDS

    def is_using_mock(self) -> bool:
        """True when real Coral discovery failed and mock schemas are active."""
        return self._using_mock

    def health_summary(self) -> dict[str, Any]:
        """
        Structured health report consumed by /ready and /sources/status.
        (Feature 16: Health Monitoring)
        """
        return {
            "schema_loaded":    bool(self._schemas),
            "table_count":      len(self._schemas),
            "using_mock":       self._using_mock,
            "loaded_at":        self._loaded_at,
            "age_seconds":      round(time.time() - self._loaded_at, 1),
            "stale":            self.is_stale(),
            "source_status":    self._source_status,
        }

    # ------------------------------------------------------------------
    # Schema Discovery & Loading — Feature 1
    # ------------------------------------------------------------------

    def load(self, coral_client: Any | None = None) -> None:
        """
        Load schemas in priority order:
          1. Fresh disk cache (if within TTL)
          2. Coral live discovery (if coral_client is provided)
          3. Mock schemas (fallback)

        (Features 1, 2, 13, 14)
        """
        if self._load_from_disk():
            logger.info(
                "schema_cache: loaded %d tables from disk cache",
                len(self._schemas),
            )
            return

        if coral_client is not None:
            if self._discover_from_coral(coral_client):
                self._persist_to_disk()
                logger.info(
                    "schema_cache: discovered %d tables from Coral",
                    len(self._schemas),
                )
                return

        # All live sources failed — fall back to mock schemas
        self._load_mock()

    def refresh(self, coral_client: Any | None = None) -> None:
        """
        Force a fresh schema discovery, bypassing the disk cache.
        Call this from the scheduled refresh or the /refresh admin endpoint.
        (Feature 10: Schema Refresh Mechanism)
        """
        logger.info("schema_cache: forcing refresh")
        CACHE_FILE.unlink(missing_ok=True)
        self._schemas.clear()
        self._loaded_at = 0.0
        self.load(coral_client)

    # ------------------------------------------------------------------
    # Validation — Features 5, 9
    # ------------------------------------------------------------------

    def validate_table(self, table: str) -> bool:
        """
        Return True if the table exists in the registry.
        Used by coral_client.py before executing a query. (Feature 9)
        """
        exists = table in self._schemas
        if not exists:
            logger.warning("schema_cache: unknown table '%s'", table)
        return exists

    def validate_column(self, table: str, column: str) -> bool:
        """
        Return True if the column exists on the given table. (Feature 5, 9)
        Handles common API drift (e.g. view_count → views).
        """
        columns = self.get_column_names(table)
        if column in columns:
            return True
        logger.warning(
            "schema_cache: column '%s' not found on '%s'. Available: %s",
            column, table, columns,
        )
        return False

    def validate_join(self, left_table: str, right_table: str) -> dict[str, str] | None:
        """
        Return the JOIN relationship descriptor if one exists, else None.
        Used by insight_engine.py to verify JOIN validity before sending to
        Coral. (Feature 7, Feature 9)
        """
        for rel in JOIN_RELATIONSHIPS:
            if (
                rel["left_table"] == left_table
                and rel["right_table"] == right_table
            ) or (
                rel["left_table"] == right_table
                and rel["right_table"] == left_table
            ):
                return rel
        logger.warning(
            "schema_cache: no known JOIN path between '%s' and '%s'",
            left_table, right_table,
        )
        return None

    def detect_missing_sources(self) -> list[str]:
        """
        Return source names whose tables are completely absent from the
        registry. Used by /ready readiness probe. (Feature 6)
        """
        missing = []
        for source in KNOWN_SOURCES:
            has_table = any(k.startswith(f"{source}.") for k in self._schemas)
            if not has_table:
                missing.append(source)
        return missing

    # ------------------------------------------------------------------
    # AI Context Enrichment — Feature 17
    # ------------------------------------------------------------------

    def as_claude_context(self) -> str:
        """
        Render the schema registry as a compact markdown block for injection
        into Claude's system prompt. Gives the LLM valid table/column names
        so it generates correct SQL instead of hallucinating column names.

        Format:
            ## Available Coral Tables

            **youtube.videos** (YouTube)
            - video_id: TEXT
            - title: TEXT
            ...

        (Features 4, 8, 17)
        """
        lines: list[str] = ["## Available Coral Tables\n"]
        for table_key, meta in sorted(self._schemas.items()):
            source = meta.get("source", "unknown")
            lines.append(f"**{table_key}** ({source.capitalize()})")
            columns = meta.get("columns", {})
            for col_name, col_meta in columns.items():
                col_type = col_meta.get("type", "UNKNOWN")
                nullable = " (nullable)" if col_meta.get("nullable") else ""
                lines.append(f"  - {col_name}: {col_type}{nullable}")
            lines.append("")

        lines.append("## Known JOIN Paths\n")
        for rel in JOIN_RELATIONSHIPS:
            lines.append(
                f"- `{rel['left_table']}` ↔ `{rel['right_table']}` "
                f"via {rel['left_key']} / {rel['right_key']} — {rel['description']}"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_from_disk(self) -> bool:
        """
        Load schema from the disk cache file if it exists and is within TTL.
        (Features 2, 11, 14)
        """
        if not CACHE_FILE.exists():
            return False

        try:
            payload: dict[str, Any] = json.loads(CACHE_FILE.read_text())
            saved_at: float = payload.get("saved_at", 0.0)
            age = time.time() - saved_at

            if age > CACHE_TTL_SECONDS:
                logger.info(
                    "schema_cache: disk cache expired (age=%.0fs > ttl=%ds)",
                    age, CACHE_TTL_SECONDS,
                )
                return False

            self._schemas = payload["schemas"]
            self._source_status = payload.get("source_status", {})
            self._loaded_at = saved_at
            self._using_mock = payload.get("using_mock", False)
            return True

        except Exception as exc:
            logger.warning("schema_cache: disk cache read failed: %s", exc)
            return False

    def _discover_from_coral(self, coral_client: Any) -> bool:
        """
        Call Coral's schema discovery API and populate the registry.
        Sets per-source status to 'healthy' or 'degraded'. (Features 1, 6)
        """
        try:
            raw: dict[str, Any] = coral_client.get_schema()
            if not raw:
                return False

            self._schemas = {}
            self._source_status = {}

            for table_key, meta in raw.items():
                # Coral returns entries like { "youtube.videos": { columns: {...} } }
                source = table_key.split(".")[0] if "." in table_key else "unknown"
                self._schemas[table_key] = {
                    "source":  source,
                    "table":   table_key.split(".", 1)[1] if "." in table_key else table_key,
                    "columns": meta.get("columns", {}),
                }

            # Mark each known source as healthy or degraded
            for source in KNOWN_SOURCES:
                has_table = any(k.startswith(f"{source}.") for k in self._schemas)
                self._source_status[source] = "healthy" if has_table else "degraded"

            self._loaded_at = time.time()
            self._using_mock = False
            return True

        except Exception as exc:
            logger.error("schema_cache: Coral discovery failed: %s", exc)
            for source in KNOWN_SOURCES:
                self._source_status[source] = "unavailable"
            return False

    def _load_mock(self) -> None:
        """
        Populate the registry with static mock schemas so SQL queries still
        resolve during demo when live APIs are unavailable. (Feature 13)
        """
        logger.warning(
            "schema_cache: falling back to mock schemas — "
            "live Coral discovery unavailable"
        )
        self._schemas = dict(MOCK_SCHEMAS)
        self._source_status = {s: "mock" for s in KNOWN_SOURCES}
        self._loaded_at = time.time()
        self._using_mock = True

    def _persist_to_disk(self) -> None:
        """
        Write the current schema registry to disk for warm restarts.
        (Features 2, 14)
        """
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at":      self._loaded_at,
                "using_mock":    self._using_mock,
                "source_status": self._source_status,
                "schemas":       self._schemas,
            }
            CACHE_FILE.write_text(json.dumps(payload, indent=2))
            logger.debug("schema_cache: persisted to %s", CACHE_FILE)
        except Exception as exc:
            logger.warning("schema_cache: disk persist failed: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# Imported everywhere as:  from coral.schema_cache import schema_cache
# ---------------------------------------------------------------------------

schema_cache = SchemaCache()
