"""
services/youtube_service.py — CreatorPulse YouTube Data Ingestion Layer

Fetches, normalises, and caches YouTube channel data.
Acts as the direct API fallback when Coral sources are unreachable,
and as the data seeder for the youtube.videos Coral table.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from config.constants import (
    MIN_WATCH_PERCENT,
    HIGH_VIEW_THRESHOLD,
    LOW_DISCORD_THRESHOLD,
    LogMsg,
    SourceStatus,
)
from config.settings import settings
from services.cache_service import cache, CacheNS

logger = logging.getLogger(__name__)

# YouTube Data API v3 base URL
_YT_BASE = "https://www.googleapis.com/youtube/v3"

# Max page size allowed by YouTube API
_PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# 17. Structured Response Models (dataclasses — no external deps needed)
# ---------------------------------------------------------------------------

@dataclass
class VideoStats:
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    favorite_count: int = 0
    engagement_rate: float = 0.0          # (likes + comments) / views


@dataclass
class VideoMeta:
    video_id: str = ""
    title: str = ""
    description: str = ""
    topic: str = ""                        # extracted content topic
    tags: list[str] = field(default_factory=list)
    duration: str = ""                     # ISO 8601 e.g. "PT12M30S"
    published_at: Optional[datetime] = None
    thumbnail_url: str = ""
    category_id: str = ""


@dataclass
class Video:
    meta: VideoMeta = field(default_factory=VideoMeta)
    stats: VideoStats = field(default_factory=VideoStats)
    is_underperformer: bool = False
    is_top_performer: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id":        self.meta.video_id,
            "title":           self.meta.title,
            "topic":           self.meta.topic,
            "tags":            self.meta.tags,
            "published_at":    self.meta.published_at.isoformat() if self.meta.published_at else None,
            "thumbnail_url":   self.meta.thumbnail_url,
            "duration":        self.meta.duration,
            "view_count":      self.stats.view_count,
            "like_count":      self.stats.like_count,
            "comment_count":   self.stats.comment_count,
            "engagement_rate": round(self.stats.engagement_rate, 4),
            "is_underperformer": self.is_underperformer,
            "is_top_performer":  self.is_top_performer,
        }


@dataclass
class ChannelTrend:
    dates: list[str] = field(default_factory=list)      # ISO date strings
    views: list[int] = field(default_factory=list)
    engagement_rates: list[float] = field(default_factory=list)
    upload_gaps_days: list[float] = field(default_factory=list)
    avg_views_per_video: float = 0.0
    avg_upload_gap_days: float = 0.0


# ---------------------------------------------------------------------------
# YouTube Service
# ---------------------------------------------------------------------------

class YouTubeService:
    """
    Async YouTube Data API v3 client.

    All public methods check the cache first.
    Falls back to mock data automatically when USE_MOCK_DATA=true
    or when the API key / channel ID is not configured.
    """

    def __init__(self) -> None:
        self._api_key:    Optional[str] = settings.youtube_api_key
        self._channel_id: Optional[str] = settings.youtube_channel_id
        self._max_results: int = settings.youtube_max_results
        self._status: str = SourceStatus.HEALTHY

    # ------------------------------------------------------------------
    # 1. Authentication guard
    # ------------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self._channel_id)

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_YT_BASE,
            params={"key": self._api_key},
            timeout=settings.api_timeout,
        )

    # ------------------------------------------------------------------
    # 16. Source Health Validation
    # ------------------------------------------------------------------

    async def health(self) -> str:
        """Return SourceStatus string for /sources/status and /ready."""
        if settings.use_mock_data:
            return SourceStatus.MOCK
        if not self.is_configured:
            return SourceStatus.OFFLINE
        return self._status

    # ------------------------------------------------------------------
    # 2. Channel Video Fetching  (with 12. Pagination + 13. Rate limiting)
    # ------------------------------------------------------------------

    async def fetch_videos(self, max_results: Optional[int] = None) -> list[Video]:
        """
        Fetch up to `max_results` videos from the configured channel.
        Results are cached; subsequent calls within TTL return instantly.
        """
        limit = max_results or self._max_results

        # 15. Cache check
        cache_key = CacheNS.GENERIC.value + f":yt_videos:{self._channel_id}:{limit}"
        cached = await cache.get(CacheNS.GENERIC, cache_key)
        if cached is not None:
            logger.debug("Cache HIT [youtube] fetch_videos channel=%s", self._channel_id)
            return cached

        # 14. Mock mode
        if settings.use_mock_data or not self.is_configured:
            return await self._mock_videos()

        logger.info(LogMsg.YOUTUBE_FETCH_START, self._channel_id)
        videos = await self._fetch_channel_videos(limit)

        await cache.set(CacheNS.GENERIC, cache_key, videos)
        self._status = SourceStatus.HEALTHY
        return videos

    async def _fetch_channel_videos(self, limit: int) -> list[Video]:
        """Internal paginated fetch — handles next-page tokens."""
        video_ids: list[str] = []
        page_token: Optional[str] = None

        try:
            async with await self._client() as client:
                # Step 1 — collect video IDs via search.list
                while len(video_ids) < limit:
                    params: dict[str, Any] = {
                        "part":       "id",
                        "channelId":  self._channel_id,
                        "type":       "video",
                        "order":      "date",
                        "maxResults": min(_PAGE_SIZE, limit - len(video_ids)),
                    }
                    if page_token:
                        params["pageToken"] = page_token

                    resp = await self._get(client, "/search", params)
                    items = resp.get("items", [])
                    video_ids += [i["id"]["videoId"] for i in items if "id" in i]

                    page_token = resp.get("nextPageToken")
                    if not page_token or not items:
                        break

                    # 13. Polite pacing to protect quota
                    await asyncio.sleep(0.1)

                # Step 2 — bulk-fetch stats + snippets (50 IDs per request)
                videos: list[Video] = []
                for chunk_start in range(0, len(video_ids), _PAGE_SIZE):
                    chunk = video_ids[chunk_start: chunk_start + _PAGE_SIZE]
                    params = {
                        "part":       "snippet,statistics,contentDetails",
                        "id":         ",".join(chunk),
                        "maxResults": _PAGE_SIZE,
                    }
                    resp = await self._get(client, "/videos", params)
                    for item in resp.get("items", []):
                        videos.append(self._parse_video(item))
                    await asyncio.sleep(0.05)

                return videos

        except Exception as exc:
            logger.warning("YouTube API error — %s; falling back to mock", exc)
            self._status = SourceStatus.DEGRADED
            return await self._mock_videos()

    # ------------------------------------------------------------------
    # 13. Rate-limit-aware GET with retry + back-off
    # ------------------------------------------------------------------

    async def _get(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        attempt: int = 1,
    ) -> dict[str, Any]:
        from config.constants import MAX_API_RETRIES, RETRY_DELAY
        try:
            r = await client.get(path, params=params)
            if r.status_code == 429:
                if attempt <= MAX_API_RETRIES:
                    wait = RETRY_DELAY * attempt
                    logger.warning("YouTube rate-limited — retry %d in %.1fs", attempt, wait)
                    await asyncio.sleep(wait)
                    return await self._get(client, path, params, attempt + 1)
                raise RuntimeError("YouTube API quota exceeded after retries")
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            if attempt <= MAX_API_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
                return await self._get(client, path, params, attempt + 1)
            raise

    # ------------------------------------------------------------------
    # 3 & 4. Video Statistics + Metadata Parsing
    # ------------------------------------------------------------------

    def _parse_video(self, item: dict[str, Any]) -> Video:
        snippet   = item.get("snippet", {})
        stats_raw = item.get("statistics", {})
        details   = item.get("contentDetails", {})

        views    = int(stats_raw.get("viewCount",    0))
        likes    = int(stats_raw.get("likeCount",    0))
        comments = int(stats_raw.get("commentCount", 0))

        # 8. Engagement rate
        eng_rate = (likes + comments) / views if views > 0 else 0.0

        thumbnails = snippet.get("thumbnails", {})
        thumb_url = (
            thumbnails.get("high", {}).get("url")
            or thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url")
            or ""
        )

        published_raw = snippet.get("publishedAt", "")
        published_at: Optional[datetime] = None
        if published_raw:
            try:
                published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            except ValueError:
                pass

        meta = VideoMeta(
            video_id     = item.get("id", ""),
            title        = snippet.get("title", ""),
            description  = snippet.get("description", "")[:500],
            topic        = self._extract_topic(snippet),
            tags         = snippet.get("tags", []),
            duration     = details.get("duration", ""),
            published_at = published_at,
            thumbnail_url = thumb_url,
            category_id  = snippet.get("categoryId", ""),
        )

        vstats = VideoStats(
            view_count     = views,
            like_count     = likes,
            comment_count  = comments,
            engagement_rate = eng_rate,
        )

        video = Video(meta=meta, stats=vstats)

        # 9 & 10. Flag underperformers and top performers inline
        video.is_underperformer = self._is_underperformer(vstats)
        video.is_top_performer  = self._is_top_performer(vstats)

        return video

    # ------------------------------------------------------------------
    # 5. Topic Extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_topic(snippet: dict[str, Any]) -> str:
        """
        Infer the video's topic from its title and tags.
        Simple keyword scan — good enough for hackathon; swap for NLP later.
        """
        TOPIC_KEYWORDS: dict[str, list[str]] = {
            "AI Agents":       ["agent", "agents", "agentic", "langgraph", "autogen"],
            "LLMs":            ["llm", "gpt", "claude", "gemini", "mistral", "language model"],
            "Python":          ["python", "django", "fastapi", "flask"],
            "DevOps":          ["docker", "kubernetes", "ci/cd", "devops", "github actions"],
            "Open Source":     ["open source", "opensource", "contributing", "oss"],
            "Career & Growth": ["career", "job", "interview", "resume", "roadmap"],
            "Tutorial":        ["tutorial", "how to", "guide", "learn", "step by step"],
        }

        text = (
            (snippet.get("title", "") + " " + " ".join(snippet.get("tags", [])))
            .lower()
        )

        for topic, keywords in TOPIC_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return topic

        return "General"

    # ------------------------------------------------------------------
    # 9. Underperformer Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_underperformer(stats: VideoStats) -> bool:
        """
        Flag: high views but low engagement — classic underperformer signal.
        (Watch % isn't available via Data API; that lives in YouTube Studio /
        Google Sheets engagement log.)
        """
        return (
            stats.view_count >= HIGH_VIEW_THRESHOLD
            and stats.engagement_rate < 0.01   # less than 1% engagement
        )

    # ------------------------------------------------------------------
    # 10. Top Performer Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_top_performer(stats: VideoStats) -> bool:
        return stats.view_count >= HIGH_VIEW_THRESHOLD and stats.engagement_rate >= 0.03

    # ------------------------------------------------------------------
    # 6. Creator Performance Trends  +  7. Upload Frequency Analysis
    # ------------------------------------------------------------------

    async def fetch_trends(self) -> ChannelTrend:
        """Return time-series view + engagement + upload-gap data."""
        cache_key = CacheNS.ANALYTICS.value + f":yt_trends:{self._channel_id}"
        cached = await cache.get(CacheNS.ANALYTICS, cache_key)
        if cached is not None:
            return cached

        videos = await self.fetch_videos()
        trend  = self._compute_trends(videos)

        await cache.set(CacheNS.ANALYTICS, cache_key, trend)
        return trend

    @staticmethod
    def _compute_trends(videos: list[Video]) -> ChannelTrend:
        # Sort oldest-first
        sorted_vids = sorted(
            [v for v in videos if v.meta.published_at],
            key=lambda v: v.meta.published_at,  # type: ignore[arg-type]
        )

        dates: list[str] = []
        views: list[int] = []
        eng_rates: list[float] = []
        upload_gaps: list[float] = []
        prev_date: Optional[datetime] = None

        for v in sorted_vids:
            dt = v.meta.published_at
            assert dt is not None
            dates.append(dt.date().isoformat())
            views.append(v.stats.view_count)
            eng_rates.append(round(v.stats.engagement_rate, 4))

            if prev_date is not None:
                gap = (dt - prev_date).total_seconds() / 86400
                upload_gaps.append(round(gap, 1))
            prev_date = dt

        avg_views = int(sum(views) / len(views)) if views else 0
        avg_gap   = round(sum(upload_gaps) / len(upload_gaps), 1) if upload_gaps else 0.0

        return ChannelTrend(
            dates=dates,
            views=views,
            engagement_rates=eng_rates,
            upload_gaps_days=upload_gaps,
            avg_views_per_video=avg_views,
            avg_upload_gap_days=avg_gap,
        )

    # ------------------------------------------------------------------
    # 3. Fetch stats for specific video IDs (used by Coral fallback)
    # ------------------------------------------------------------------

    async def fetch_video_stats(self, video_ids: list[str]) -> dict[str, VideoStats]:
        """Return {video_id: VideoStats} for a given list of IDs."""
        if settings.use_mock_data or not self.is_configured:
            mock = await cache.get_mock("youtube")
            if mock:
                return {
                    v["video_id"]: VideoStats(
                        view_count=v.get("view_count", 0),
                        like_count=v.get("like_count", 0),
                        comment_count=v.get("comment_count", 0),
                    )
                    for v in mock.get("videos", [])
                    if v["video_id"] in video_ids
                }
            return {}

        try:
            async with await self._client() as client:
                resp = await self._get(client, "/videos", {
                    "part":       "statistics",
                    "id":         ",".join(video_ids[:_PAGE_SIZE]),
                    "maxResults": _PAGE_SIZE,
                })
            result: dict[str, VideoStats] = {}
            for item in resp.get("items", []):
                vid  = item["id"]
                s    = item.get("statistics", {})
                views   = int(s.get("viewCount",    0))
                likes   = int(s.get("likeCount",    0))
                comments = int(s.get("commentCount", 0))
                result[vid] = VideoStats(
                    view_count=views,
                    like_count=likes,
                    comment_count=comments,
                    engagement_rate=(likes + comments) / views if views else 0.0,
                )
            return result
        except Exception as exc:
            logger.warning("fetch_video_stats failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # 14. Mock Mode Fallback
    # ------------------------------------------------------------------

    async def _mock_videos(self) -> list[Video]:
        """Load and parse youtube_mock.json as Video objects."""
        data = await cache.get_mock("youtube")
        if not data:
            logger.warning("No mock YouTube data available — returning empty list")
            return []

        videos: list[Video] = []
        for raw in data.get("videos", []):
            published_at: Optional[datetime] = None
            if raw.get("published_at"):
                try:
                    published_at = datetime.fromisoformat(raw["published_at"])
                except ValueError:
                    pass

            meta = VideoMeta(
                video_id     = raw.get("video_id", ""),
                title        = raw.get("title", ""),
                topic        = raw.get("topic", "General"),
                tags         = raw.get("tags", []),
                published_at = published_at,
                thumbnail_url = raw.get("thumbnail_url", ""),
                duration     = raw.get("duration", ""),
            )
            vstats = VideoStats(
                view_count      = raw.get("view_count", 0),
                like_count      = raw.get("like_count", 0),
                comment_count   = raw.get("comment_count", 0),
                engagement_rate = raw.get("engagement_rate", 0.0),
            )
            video = Video(meta=meta, stats=vstats)
            video.is_underperformer = self._is_underperformer(vstats)
            video.is_top_performer  = self._is_top_performer(vstats)
            videos.append(video)

        logger.info("Loaded %d mock YouTube videos", len(videos))
        return videos

    # ------------------------------------------------------------------
    # Convenience: top-N and underperformers lists
    # ------------------------------------------------------------------

    async def top_videos(self, n: int = 5) -> list[Video]:
        videos = await self.fetch_videos()
        return sorted(videos, key=lambda v: v.stats.view_count, reverse=True)[:n]

    async def underperforming_videos(self) -> list[Video]:
        videos = await self.fetch_videos()
        return [v for v in videos if v.is_underperformer]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
youtube_service = YouTubeService()
