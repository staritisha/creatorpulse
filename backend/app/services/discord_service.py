"""
services/discord_service.py — CreatorPulse Community Intelligence Engine

Fetches, analyses, and structures Discord community activity.
Powers the community-resonance half of the CreatorPulse Resonance Score by
correlating video uploads with audience discussion bursts.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from config.constants import (
    BASELINE_DAYS,
    COMMUNITY_BURST_LIMIT,
    SPIKE_MULTIPLIER,
    SPIKE_WINDOW_HOURS,
    LogMsg,
    SourceStatus,
    MAX_API_RETRIES,
    RETRY_DELAY,
)
from config.settings import settings
from services.cache_service import cache, CacheNS

logger = logging.getLogger(__name__)

_DISCORD_BASE = "https://discord.com/api/v10"
_PAGE_LIMIT   = 100   # max messages per Discord API request


# ---------------------------------------------------------------------------
# 18. Structured Response Models
# ---------------------------------------------------------------------------

@dataclass
class DiscordMessage:
    message_id: str
    channel_id: str
    author_id: str
    author_name: str
    content: str
    timestamp: datetime
    reply_count: int = 0
    reaction_count: int = 0
    topic: str = "General"
    sentiment: str = "neutral"        # positive | negative | neutral | confused


@dataclass
class DailyActivity:
    date: str                         # ISO date
    message_count: int = 0
    unique_authors: int = 0
    is_spike: bool = False
    is_burst: bool = False
    spike_ratio: float = 1.0          # count / baseline


@dataclass
class CommunityMetrics:
    guild_id: str
    total_messages: int = 0
    unique_authors: int = 0
    daily_activity: list[DailyActivity] = field(default_factory=list)
    top_topics: list[dict[str, Any]] = field(default_factory=list)
    sentiment_summary: dict[str, int] = field(default_factory=dict)
    has_loyal_audience: bool = False
    is_silent_audience: bool = False
    spike_days: list[str] = field(default_factory=list)
    burst_days: list[str] = field(default_factory=list)
    avg_daily_messages: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "guild_id":          self.guild_id,
            "total_messages":    self.total_messages,
            "unique_authors":    self.unique_authors,
            "avg_daily_messages": round(self.avg_daily_messages, 1),
            "top_topics":        self.top_topics,
            "sentiment_summary": self.sentiment_summary,
            "has_loyal_audience": self.has_loyal_audience,
            "is_silent_audience": self.is_silent_audience,
            "spike_days":        self.spike_days,
            "burst_days":        self.burst_days,
            "daily_activity":    [
                {
                    "date":         d.date,
                    "message_count": d.message_count,
                    "unique_authors": d.unique_authors,
                    "is_spike":     d.is_spike,
                    "is_burst":     d.is_burst,
                    "spike_ratio":  round(d.spike_ratio, 2),
                }
                for d in self.daily_activity
            ],
        }


@dataclass
class VideoMentionCorrelation:
    video_id: str
    video_title: str
    mention_count: int = 0
    first_mention: Optional[datetime] = None
    peak_mention_day: Optional[str] = None
    related_messages: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discord Service
# ---------------------------------------------------------------------------

class DiscordService:
    """
    Async Discord Bot API client.

    Fetches messages from configured guild channels, computes activity
    metrics, detects spikes/bursts, extracts topics and sentiment, and
    correlates discussion with video uploads.
    """

    def __init__(self) -> None:
        self._token:       Optional[str]       = settings.discord_bot_token
        self._guild_id:    Optional[str]       = settings.discord_guild_id
        self._channel_ids: list[str]           = settings.discord_channel_ids
        self._status:      str                 = SourceStatus.HEALTHY

    # ------------------------------------------------------------------
    # 1. Authentication guard
    # ------------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._guild_id)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bot {self._token}"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_DISCORD_BASE,
            headers=self._headers(),
            timeout=settings.api_timeout,
        )

    # ------------------------------------------------------------------
    # 17. Source Health Monitoring
    # ------------------------------------------------------------------

    async def health(self) -> str:
        if settings.use_mock_data:
            return SourceStatus.MOCK
        if not self.is_configured:
            return SourceStatus.OFFLINE
        return self._status

    # ------------------------------------------------------------------
    # 2. Community Message Fetching  (with 13. Channel Filtering)
    # ------------------------------------------------------------------

    async def fetch_messages(
        self,
        days: int = BASELINE_DAYS,
        channel_ids: Optional[list[str]] = None,
    ) -> list[DiscordMessage]:
        """
        Fetch messages from configured (or specified) channels
        for the last `days` days.  Results are cached.
        """
        channels = channel_ids or self._channel_ids
        cache_key = f"discord_msgs:{self._guild_id}:{days}:{','.join(sorted(channels))}"

        # 16. Cache check
        cached = await cache.get(CacheNS.GENERIC, cache_key)
        if cached is not None:
            logger.debug("Cache HIT [discord] fetch_messages guild=%s", self._guild_id)
            return cached

        # 15. Mock fallback
        if settings.use_mock_data or not self.is_configured:
            return await self._mock_messages()

        logger.info(LogMsg.DISCORD_FETCH_START, self._guild_id)

        since = datetime.now(timezone.utc) - timedelta(days=days)
        messages: list[DiscordMessage] = []

        for channel_id in channels:
            channel_msgs = await self._fetch_channel_messages(channel_id, since)
            messages.extend(channel_msgs)

        # Enrich with topic + sentiment
        for msg in messages:
            msg.topic     = self._extract_topic(msg.content)
            msg.sentiment = self._analyze_sentiment(msg.content)

        await cache.set(CacheNS.GENERIC, cache_key, messages)
        self._status = SourceStatus.HEALTHY
        return messages

    async def _fetch_channel_messages(
        self,
        channel_id: str,
        since: datetime,
    ) -> list[DiscordMessage]:
        """Paginate through a channel's history back to `since`."""
        messages: list[DiscordMessage] = []
        before_id: Optional[str] = None

        try:
            async with self._client() as client:
                while True:
                    params: dict[str, Any] = {"limit": _PAGE_LIMIT}
                    if before_id:
                        params["before"] = before_id

                    data = await self._get(client, f"/channels/{channel_id}/messages", params)
                    if not data:
                        break

                    for raw in data:
                        msg = self._parse_message(raw, channel_id)
                        if msg.timestamp < since:
                            return messages          # reached the time boundary
                        messages.append(msg)

                    if len(data) < _PAGE_LIMIT:
                        break                        # no more pages

                    before_id = data[-1]["id"]
                    await asyncio.sleep(0.1)         # 14. polite pacing

        except Exception as exc:
            logger.warning("Discord channel %s fetch failed: %s", channel_id, exc)
            self._status = SourceStatus.DEGRADED

        return messages

    # ------------------------------------------------------------------
    # 14. Rate-limit-aware GET with retry + back-off
    # ------------------------------------------------------------------

    async def _get(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        attempt: int = 1,
    ) -> Any:
        try:
            r = await client.get(path, params=params)

            if r.status_code == 429:
                retry_after = float(r.headers.get("Retry-After", RETRY_DELAY * attempt))
                logger.warning("Discord rate-limited — retry %d in %.1fs", attempt, retry_after)
                await asyncio.sleep(retry_after)
                if attempt <= MAX_API_RETRIES:
                    return await self._get(client, path, params, attempt + 1)
                raise RuntimeError("Discord API rate-limit retries exhausted")

            if r.status_code == 403:
                logger.warning("Discord 403 on %s — missing bot permissions", path)
                return []

            r.raise_for_status()
            return r.json()

        except httpx.TimeoutException:
            if attempt <= MAX_API_RETRIES:
                await asyncio.sleep(RETRY_DELAY * attempt)
                return await self._get(client, path, params, attempt + 1)
            raise

    # ------------------------------------------------------------------
    # 4. Message parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_message(raw: dict[str, Any], channel_id: str) -> DiscordMessage:
        ts_raw = raw.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        author  = raw.get("author", {})
        content = raw.get("content", "")

        reactions = sum(
            r.get("count", 0) for r in raw.get("reactions", [])
        )

        return DiscordMessage(
            message_id     = raw.get("id", ""),
            channel_id     = channel_id,
            author_id      = author.get("id", ""),
            author_name    = author.get("username", "unknown"),
            content        = content,
            timestamp      = ts,
            reply_count    = raw.get("referenced_message", {}) and 1 or 0,
            reaction_count = reactions,
        )

    # ------------------------------------------------------------------
    # 3. Channel Activity Analysis  +  12. Time-Based Analysis
    # ------------------------------------------------------------------

    async def fetch_daily_activity(
        self,
        days: int = BASELINE_DAYS,
    ) -> list[DailyActivity]:
        """Return per-day message counts with spike / burst flags."""
        cache_key = f"discord_daily:{self._guild_id}:{days}"
        cached = await cache.get(CacheNS.ANALYTICS, cache_key)
        if cached is not None:
            return cached

        messages = await self.fetch_messages(days=days)
        activity = self._compute_daily_activity(messages)

        await cache.set(CacheNS.ANALYTICS, cache_key, activity)
        return activity

    @staticmethod
    def _compute_daily_activity(messages: list[DiscordMessage]) -> list[DailyActivity]:
        counts_by_date: dict[str, list[str]] = defaultdict(list)   # date → [author_ids]
        for msg in messages:
            day = msg.timestamp.date().isoformat()
            counts_by_date[day].append(msg.author_id)

        if not counts_by_date:
            return []

        sorted_days = sorted(counts_by_date.keys())
        daily_counts = [len(counts_by_date[d]) for d in sorted_days]
        baseline = (
            sum(daily_counts) / len(daily_counts) if daily_counts else 1.0
        )

        result: list[DailyActivity] = []
        for day, authors in sorted(counts_by_date.items()):
            count      = len(authors)
            ratio      = count / baseline if baseline > 0 else 1.0
            is_spike   = ratio >= SPIKE_MULTIPLIER
            is_burst   = ratio >= COMMUNITY_BURST_LIMIT

            result.append(DailyActivity(
                date           = day,
                message_count  = count,
                unique_authors = len(set(authors)),
                is_spike       = is_spike,
                is_burst       = is_burst,
                spike_ratio    = ratio,
            ))

        return result

    # ------------------------------------------------------------------
    # 4. Community Spike Detection
    # ------------------------------------------------------------------

    async def spike_days(self, days: int = BASELINE_DAYS) -> list[str]:
        activity = await self.fetch_daily_activity(days=days)
        return [d.date for d in activity if d.is_spike]

    async def burst_days(self, days: int = BASELINE_DAYS) -> list[str]:
        activity = await self.fetch_daily_activity(days=days)
        return [d.date for d in activity if d.is_burst]

    async def messages_around_date(
        self,
        target_date: datetime,
        window_hours: int = SPIKE_WINDOW_HOURS,
    ) -> list[DiscordMessage]:
        """Return messages within ±window_hours of a given datetime."""
        delta = timedelta(hours=window_hours)
        messages = await self.fetch_messages(days=BASELINE_DAYS)
        return [
            m for m in messages
            if abs((m.timestamp - target_date).total_seconds()) <= delta.total_seconds()
        ]

    # ------------------------------------------------------------------
    # 5. Message Sentiment Analysis
    # ------------------------------------------------------------------

    _POSITIVE_WORDS = {
        "amazing", "great", "awesome", "love", "excellent", "helpful",
        "fantastic", "brilliant", "learned", "understand", "clear",
        "easy", "perfect", "wonderful", "best", "thanks", "thank",
    }
    _NEGATIVE_WORDS = {
        "confused", "confusing", "unclear", "broken", "wrong", "hard",
        "difficult", "error", "bug", "crash", "doesn't work", "not working",
        "bad", "worst", "hate", "frustrating", "lost",
    }
    _EXCITED_WORDS = {
        "!", "omg", "wow", "incredible", "mind-blowing", "insane", "fire",
        "finally", "waited", "can't wait",
    }

    @classmethod
    def _analyze_sentiment(cls, text: str) -> str:
        lower = text.lower()
        pos = sum(1 for w in cls._POSITIVE_WORDS if w in lower)
        neg = sum(1 for w in cls._NEGATIVE_WORDS if w in lower)
        exc = sum(1 for w in cls._EXCITED_WORDS if w in lower)

        if exc >= 2 or pos >= 3:
            return "positive"
        if neg >= 2:
            return "negative"
        if "?" in text and neg >= 1:
            return "confused"
        if pos > neg:
            return "positive"
        if neg > pos:
            return "negative"
        return "neutral"

    # ------------------------------------------------------------------
    # 6. Topic Extraction from community messages
    # ------------------------------------------------------------------

    _TOPIC_KEYWORDS: dict[str, list[str]] = {
        "AI Agents":       ["agent", "agents", "langgraph", "autogen", "agentic"],
        "LLMs":            ["llm", "gpt", "claude", "gemini", "language model"],
        "Python":          ["python", "pip", "django", "fastapi", "flask"],
        "DevOps":          ["docker", "kubernetes", "github actions", "ci/cd", "deploy"],
        "Career & Growth": ["job", "career", "interview", "resume", "hired", "roadmap"],
        "Open Source":     ["open source", "pr", "pull request", "contribute", "fork"],
        "Tutorial":        ["tutorial", "how to", "guide", "explain", "walkthrough"],
    }

    @classmethod
    def _extract_topic(cls, text: str) -> str:
        lower = text.lower()
        for topic, keywords in cls._TOPIC_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                return topic
        return "General"

    # ------------------------------------------------------------------
    # 11. Trend Detection in Conversations
    # ------------------------------------------------------------------

    async def top_topics(
        self,
        days: int = BASELINE_DAYS,
        top_n: int = 5,
    ) -> list[dict[str, Any]]:
        """Return top_n most-discussed topics with message counts."""
        cache_key = f"discord_topics:{self._guild_id}:{days}"
        cached = await cache.get(CacheNS.ANALYTICS, cache_key)
        if cached is not None:
            return cached

        messages = await self.fetch_messages(days=days)
        topic_counter: Counter = Counter(m.topic for m in messages)
        result = [
            {"topic": topic, "message_count": count}
            for topic, count in topic_counter.most_common(top_n)
        ]

        await cache.set(CacheNS.ANALYTICS, cache_key, result)
        return result

    # ------------------------------------------------------------------
    # 7. Video Mention Correlation
    # ------------------------------------------------------------------

    async def correlate_with_videos(
        self,
        videos: list[dict[str, Any]],   # list of {"video_id", "title", "published_at"}
        days: int = BASELINE_DAYS,
    ) -> list[VideoMentionCorrelation]:
        """
        Find Discord messages that mention each video by title keywords
        and return a correlation object per video.
        """
        messages = await self.fetch_messages(days=days)
        correlations: list[VideoMentionCorrelation] = []

        for video in videos:
            vid_id    = video.get("video_id", "")
            title     = video.get("title", "")
            keywords  = self._title_keywords(title)

            matched: list[DiscordMessage] = [
                m for m in messages
                if any(kw in m.content.lower() for kw in keywords)
            ]

            if not matched:
                correlations.append(
                    VideoMentionCorrelation(video_id=vid_id, video_title=title)
                )
                continue

            matched_sorted = sorted(matched, key=lambda m: m.timestamp)
            peak_day = self._peak_mention_day(matched)

            correlations.append(VideoMentionCorrelation(
                video_id       = vid_id,
                video_title    = title,
                mention_count  = len(matched),
                first_mention  = matched_sorted[0].timestamp,
                peak_mention_day = peak_day,
                related_messages = [m.content[:120] for m in matched[:5]],
            ))

        return correlations

    @staticmethod
    def _title_keywords(title: str) -> list[str]:
        """Extract 2+ word content keywords from a video title."""
        stop = {"the", "a", "an", "and", "or", "how", "to", "with", "for", "in", "my"}
        words = [w.lower() for w in re.split(r"[\s\-|:]+", title) if w.lower() not in stop]
        return [w for w in words if len(w) > 3]

    @staticmethod
    def _peak_mention_day(messages: list[DiscordMessage]) -> Optional[str]:
        day_counter: Counter = Counter(m.timestamp.date().isoformat() for m in messages)
        if not day_counter:
            return None
        return day_counter.most_common(1)[0][0]

    # ------------------------------------------------------------------
    # 8. Community Engagement Metrics (full summary)
    # ------------------------------------------------------------------

    async def fetch_community_metrics(self, days: int = BASELINE_DAYS) -> CommunityMetrics:
        """Return a full CommunityMetrics object used by analytics routes."""
        cache_key = f"discord_metrics:{self._guild_id}:{days}"
        cached = await cache.get(CacheNS.ANALYTICS, cache_key)
        if cached is not None:
            return cached

        messages  = await self.fetch_messages(days=days)
        activity  = self._compute_daily_activity(messages)
        topics    = await self.top_topics(days=days)

        sentiment_counter: Counter = Counter(m.sentiment for m in messages)
        all_authors = [m.author_id for m in messages]
        author_freq: Counter = Counter(all_authors)

        # 9. Loyal audience = ≥30% of active members posted on 3+ separate days
        loyal_threshold   = 3
        loyal_authors     = sum(1 for _, cnt in author_freq.items() if cnt >= loyal_threshold)
        has_loyal_audience = (
            (loyal_authors / len(author_freq) >= 0.30) if author_freq else False
        )

        # 10. Silent audience = avg daily messages < LOW_DISCORD_THRESHOLD
        from config.constants import LOW_DISCORD_THRESHOLD
        avg_daily = len(messages) / days if days else 0.0
        is_silent = avg_daily < LOW_DISCORD_THRESHOLD

        spikes = [d.date for d in activity if d.is_spike]
        bursts = [d.date for d in activity if d.is_burst]

        metrics = CommunityMetrics(
            guild_id           = self._guild_id or "",
            total_messages     = len(messages),
            unique_authors     = len(set(all_authors)),
            daily_activity     = activity,
            top_topics         = topics,
            sentiment_summary  = dict(sentiment_counter),
            has_loyal_audience = has_loyal_audience,
            is_silent_audience = is_silent,
            spike_days         = spikes,
            burst_days         = bursts,
            avg_daily_messages = avg_daily,
        )

        await cache.set(CacheNS.ANALYTICS, cache_key, metrics)
        return metrics

    # ------------------------------------------------------------------
    # 15. Mock Mode Fallback
    # ------------------------------------------------------------------

    async def _mock_messages(self) -> list[DiscordMessage]:
        data = await cache.get_mock("discord")
        if not data:
            logger.warning("No mock Discord data available — returning empty list")
            return []

        messages: list[DiscordMessage] = []
        for raw in data.get("messages", []):
            try:
                ts = datetime.fromisoformat(raw.get("timestamp", "").replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = datetime.now(timezone.utc)

            content = raw.get("content", "")
            messages.append(DiscordMessage(
                message_id     = raw.get("message_id", ""),
                channel_id     = raw.get("channel_id", ""),
                author_id      = raw.get("author_id", ""),
                author_name    = raw.get("author_name", "unknown"),
                content        = content,
                timestamp      = ts,
                reply_count    = raw.get("reply_count", 0),
                reaction_count = raw.get("reaction_count", 0),
                topic          = self._extract_topic(content),
                sentiment      = self._analyze_sentiment(content),
            ))

        logger.info("Loaded %d mock Discord messages", len(messages))
        return messages

    # ------------------------------------------------------------------
    # Convenience helpers used by insight_engine
    # ------------------------------------------------------------------

    async def message_count_for_date(self, date_str: str) -> int:
        activity = await self.fetch_daily_activity()
        for d in activity:
            if d.date == date_str:
                return d.message_count
        return 0

    async def baseline_daily_count(self, days: int = BASELINE_DAYS) -> float:
        activity = await self.fetch_daily_activity(days=days)
        if not activity:
            return 0.0
        return sum(d.message_count for d in activity) / len(activity)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
discord_service = DiscordService()
