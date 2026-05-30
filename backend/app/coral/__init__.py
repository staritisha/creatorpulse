"""
coral — Coral cross-source SQL client and schema cache.

Coral lets CreatorPulse JOIN YouTube, Discord, and Google Sheets data
in a single SQL query.  This package provides:

* coral_client   CoralClient singleton — async query runner with retry,
                 timeout, SQL safety checks, and mock fallback
* schema_cache   SchemaCache singleton — disk-backed schema introspection
                 with TTL invalidation

Typical usage:
    from coral.coral_client import coral_client, QueryResult
    from coral.schema_cache import schema_cache
"""
from coral.coral_client import (
    CoralClient,
    QueryResult,
    coral_client,
)
from coral.schema_cache import (
    SchemaCache,
    schema_cache,
)

__all__ = [
    "CoralClient", "QueryResult", "coral_client",
    "SchemaCache", "schema_cache",
]
