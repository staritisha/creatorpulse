"""
services — data ingestion and caching layer for CreatorPulse.

Each service exposes a module-level singleton instance so callers never
need to construct their own:

    from services.cache_service   import cache
    from services.youtube_service import youtube_service
    from services.discord_service import discord_service
    from services.sheets_service  import sheets_service

The coral_service module is a collection of sync helper functions (not a
class), imported directly:

    from services.coral_service import query_resonance, query_trends
"""
from services.cache_service import (
    CacheService,
    CacheNS,
    cache,
)
from services.youtube_service import (
    YouTubeService,
    Video,
    VideoMeta,
    VideoStats,
    ChannelTrend,
    youtube_service,
)
from services.discord_service import (
    DiscordService,
    DiscordMessage,
    DailyActivity,
    CommunityMetrics,
    VideoMentionCorrelation,
    discord_service,
)
from services.sheets_service import (
    SheetsService,
    EngagementRow,
    CreatorGoal,
    ContentExperiment,
    SheetsContext,
    sheets_service,
)
from services.coral_service import (
    query_resonance,
    query_trends,
    query_underperformers,
    query_engagement,
    ping,
    get_schema,
)

__all__ = [
    # cache
    "CacheService", "CacheNS", "cache",
    # youtube
    "YouTubeService", "Video", "VideoMeta", "VideoStats", "ChannelTrend", "youtube_service",
    # discord
    "DiscordService", "DiscordMessage", "DailyActivity", "CommunityMetrics",
    "VideoMentionCorrelation", "discord_service",
    # sheets
    "SheetsService", "EngagementRow", "CreatorGoal", "ContentExperiment",
    "SheetsContext", "sheets_service",
    # coral_service (functions)
    "query_resonance", "query_trends", "query_underperformers", "query_engagement",
    "ping", "get_schema",
]
