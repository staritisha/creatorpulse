"""
models/response_models.py
CreatorPulse · API Response Structure System

Role: Define every response shape returned by the CreatorPulse API.
      All routes import from here — zero ad-hoc JSON dicts in route handlers.

Design goals:
  - Consistent envelope: success / message / data / timestamp / metadata
  - Typed Pydantic v2 models for FastAPI auto-docs + validation
  - Serialisable to JSON with a single .model_dump()
  - Every model has a factory classmethod for easy construction
  - Mock factories on every model for testing and demo reliability

Used by:
  routes/chat.py       routes/analytics.py
  routes/insights.py   routes/sources.py
  routes/health.py     main.py
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

DataT = TypeVar("DataT")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_confidence(v: float) -> float:
    """Clamp to [0, 100] and return as a percentage integer-ish float."""
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return round(max(0.0, min(1.0, v)) * 100, 1)


# ===========================================================================
# 1. Standard API envelope (Feature 1)
# ===========================================================================

class APIResponse(BaseModel, Generic[DataT]):
    """
    Universal response wrapper for every CreatorPulse endpoint.

    Shape:
      {
        "success":   true,
        "message":   "Insights generated successfully",
        "data":      { ... },
        "timestamp": "2026-05-29T12:30:00+00:00",
        "metadata":  { "latency_ms": 340, "model": "claude-opus-4-5" }
      }
    """
    success:   bool            = True
    message:   str             = "OK"
    data:      DataT | None    = None
    timestamp: str             = Field(default_factory=_now_iso)
    metadata:  dict[str, Any]  = Field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        data:     DataT,
        message:  str             = "OK",
        metadata: dict[str, Any]  = {},
    ) -> "APIResponse[DataT]":
        return cls(success=True, message=message, data=data, metadata=metadata)

    @classmethod
    def fail(
        cls,
        message:  str            = "An error occurred",
        metadata: dict[str, Any] = {},
    ) -> "APIResponse[None]":
        return cls(success=False, message=message, data=None, metadata=metadata)


# ===========================================================================
# 6. Error response (Feature 6)
# ===========================================================================

class ErrorResponse(BaseModel):
    """
    Standardised error envelope — used by the global exception handler in main.py.

    {
      "success": false,
      "error":   "coral_connection_failed",
      "message": "Unable to fetch analytics data",
      "details": { "retries": 3 },
      "path":    "/api/insights/top",
      "timestamp": "..."
    }
    """
    success:   bool             = False
    error:     str              = "internal_error"
    message:   str              = "An unexpected error occurred"
    details:   dict[str, Any]   = Field(default_factory=dict)
    path:      str              = ""
    timestamp: str              = Field(default_factory=_now_iso)

    @classmethod
    def from_exception(
        cls,
        exc:     Exception,
        path:    str = "",
        code:    str = "internal_error",
    ) -> "ErrorResponse":
        return cls(
            error   = code,
            message = str(exc),
            path    = path,
        )


# ===========================================================================
# 9. Confidence score (Feature 9)
# ===========================================================================

class ConfidenceScore(BaseModel):
    """
    Reusable confidence sub-model embedded in insights, forecasts, recommendations.
    confidence is always expressed as 0–100 (percent).
    """
    confidence:  float  = Field(ge=0, le=100)
    label:       str    = "moderate"   # high | moderate | low
    reason:      str    = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalise(cls, v: float) -> float:
        # Accept both 0–1 fractions and 0–100 percentages
        if 0.0 < v <= 1.0:
            return round(v * 100, 1)
        return round(max(0.0, min(100.0, float(v))), 1)

    @model_validator(mode="after")
    def _set_label(self) -> "ConfidenceScore":
        if not self.label or self.label == "moderate":
            if self.confidence >= 80:
                self.label = "high"
            elif self.confidence >= 60:
                self.label = "moderate"
            else:
                self.label = "low"
        return self

    @classmethod
    def from_float(cls, value: float, reason: str = "") -> "ConfidenceScore":
        return cls(confidence=value, reason=reason)


# ===========================================================================
# 5. Recommendation response model (Feature 5)
# ===========================================================================

class RecommendationModel(BaseModel):
    """
    Serialisable version of ai/recommendations.Recommendation.
    Maps directly to a frontend dashboard card.
    """
    category:           str   = ""
    priority:           str   = "medium"    # high | medium | low
    title:              str   = ""
    action:             str   = ""
    explanation:        str   = ""
    supporting_metrics: list[str]          = Field(default_factory=list)
    expected_impact:    str   = ""
    confidence:         ConfidenceScore    = Field(default_factory=lambda: ConfidenceScore(confidence=65))
    topic:              str | None         = None
    related_videos:     list[str]          = Field(default_factory=list)

    @classmethod
    def from_rec(cls, rec: Any) -> "RecommendationModel":
        """Convert a recommendations.Recommendation dataclass instance."""
        return cls(
            category           = rec.category.value if hasattr(rec.category, "value") else str(rec.category),
            priority           = rec.priority.value  if hasattr(rec.priority,  "value") else str(rec.priority),
            title              = rec.title,
            action             = rec.action,
            explanation        = rec.explanation,
            supporting_metrics = rec.supporting_metrics,
            expected_impact    = rec.expected_impact,
            confidence         = ConfidenceScore(confidence=rec.confidence, label=rec.confidence_label),
            topic              = rec.topic,
            related_videos     = rec.related_videos,
        )

    @classmethod
    def mock(cls) -> "RecommendationModel":
        return cls(
            category    = "content_strategy",
            priority    = "high",
            title       = "Double down on AI Agents",
            action      = "Publish 2 AI Agent tutorials in the next 3 weeks",
            explanation = "AI Agents content averages 84 resonance — 31 pts above channel average",
            supporting_metrics = ["AI Agents resonance: 84/100", "Channel avg: 53/100"],
            expected_impact    = "~+18% resonance gain over 30 days",
            confidence  = ConfidenceScore(confidence=91, label="high"),
            topic       = "AI Agents",
        )


# ===========================================================================
# 2. Chat response model (Feature 2)
# ===========================================================================

class ChatResponse(BaseModel):
    """
    AI chat answer returned by routes/chat.py (non-streaming path).
    Streaming path uses SSE text chunks — no model needed.
    """
    answer:           str   = ""
    summary:          str   = ""
    key_insight:      str   = ""
    signals:          list[str]                = Field(default_factory=list)
    recommendations:  list[RecommendationModel]= Field(default_factory=list)
    confidence:       ConfidenceScore          = Field(default_factory=lambda: ConfidenceScore(confidence=70))
    intent:           str   = "general_chat"
    model_used:       str   = ""
    from_mock:        bool  = False
    from_cache:       bool  = False
    latency_ms:       int   = 0

    @classmethod
    def from_insight_response(cls, ir: Any) -> "ChatResponse":
        """Convert an insight_engine.InsightResponse to ChatResponse."""
        recs: list[RecommendationModel] = []
        if ir.recommendations and ir.recommendations.recommendations:
            recs = [RecommendationModel.from_rec(r) for r in ir.recommendations.recommendations[:5]]

        return cls(
            answer       = ir.key_insight or ir.summary,
            summary      = ir.summary,
            key_insight  = ir.key_insight,
            signals      = ir.signals,
            recommendations = recs,
            confidence   = ConfidenceScore(confidence=ir.confidence, label=ir.confidence_label),
            intent       = ir.intent,
            model_used   = ir.model_used,
            from_mock    = ir.from_mock,
            from_cache   = ir.from_cache,
            latency_ms   = ir.latency_ms,
        )

    @classmethod
    def mock(cls) -> "ChatResponse":
        return cls(
            answer      = "AI tutorials are your strongest growth lever right now.",
            summary     = "AI Agents content outperforms your channel average by 31 resonance points.",
            key_insight = (
                "AI Agent tutorials average 84 resonance driven by 68% retention and "
                "3.2× Discord spike activity — your highest-signal content category."
            ),
            signals      = [
                "🔥 Viral candidate: 'Building an AI Agent from Scratch' — 4.1× Discord spike",
                "📈 Rising topic: 'AI Agents'",
                "⚠ Underperformer: 'Career Q&A #12' — ctr retention mismatch",
            ],
            recommendations = [RecommendationModel.mock()],
            confidence   = ConfidenceScore(confidence=88, label="high"),
            intent       = "content_recommendation",
            model_used   = "mock",
            from_mock    = True,
        )


# ===========================================================================
# 10. Trend response model (Feature 10)
# ===========================================================================

class TrendModel(BaseModel):
    topic:      str   = ""
    momentum:   str   = "stable"    # rising | stable | declining
    direction:  float = 0.0         # resonance delta
    resonance:  float = 0.0
    video_count: int  = 0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TrendModel":
        delta = float(row.get("resonance_delta", 0))
        momentum = "rising" if delta > 3 else "declining" if delta < -3 else "stable"
        return cls(
            topic       = str(row.get("topic", "")),
            momentum    = momentum,
            direction   = round(delta, 2),
            resonance   = float(row.get("resonance_score", 0)),
            video_count = int(row.get("video_count", 0)),
        )


# ===========================================================================
# 11. Underperformer response model (Feature 11)
# ===========================================================================

class UnderperformerModel(BaseModel):
    video_id:       str   = ""
    title:          str   = ""
    topic:          str   = ""
    resonance_score: float = 0.0
    watch_pct:      float = 0.0
    discord_msgs:   int   = 0
    cause:          str   = ""
    recommendation: str   = ""
    priority:       str   = "high"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "UnderperformerModel":
        diagnosis = str(row.get("primary_diagnosis", "low_retention"))
        rec_map = {
            "low_retention":          "Tighten the first 30 seconds — retention drops sharply in the intro",
            "ctr_retention_mismatch": "Deliver the thumbnail promise within the first 60 seconds",
            "community_silence":      "Add a discussion prompt to the video and pin it in Discord",
            "false_popularity":       "Reframe title and thumbnail to set accurate viewer expectations",
            "weak_engagement":        "Add mid-video calls to action (polls, comment prompts, timestamps)",
        }
        return cls(
            video_id        = str(row.get("video_id", "")),
            title           = str(row.get("title", "")),
            topic           = str(row.get("topic", "")),
            resonance_score = float(row.get("resonance_score", 0)),
            watch_pct       = float(row.get("watch_pct", 0)),
            discord_msgs    = int(row.get("discord_msg_count", 0)),
            cause           = diagnosis.replace("_", " ").title(),
            recommendation  = rec_map.get(diagnosis, "Review hook and pacing"),
        )


# ===========================================================================
# 12. Audience health model (Feature 12)
# ===========================================================================

class AudienceHealthModel(BaseModel):
    health_score:         float = 0.0
    health_label:         str   = "unknown"
    community_loyalty:    float = 0.0
    avg_retention_pct:    float = 0.0
    sentiment:            str   = "neutral"
    flag_passive_audience: bool = False
    flag_burnout:         bool  = False
    strong_signals:       list[str] = Field(default_factory=list)
    weak_signals:         list[str] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AudienceHealthModel":
        return cls(
            health_score          = float(d.get("health_score", 0)),
            health_label          = str(d.get("health_label", "unknown")),
            community_loyalty     = float(d.get("community_loyalty_index", 0)),
            avg_retention_pct     = float(d.get("avg_retention_pct", 0)),
            sentiment             = str(d.get("overall_sentiment", "neutral")),
            flag_passive_audience = bool(d.get("flag_passive_audience", False)),
            flag_burnout          = bool(d.get("flag_burnout", False)),
            strong_signals        = list(d.get("strong_signals", [])),
            weak_signals          = list(d.get("weak_signals", [])),
        )

    @classmethod
    def mock(cls) -> "AudienceHealthModel":
        return cls(
            health_score       = 73.0,
            health_label       = "healthy",
            community_loyalty  = 68.0,
            avg_retention_pct  = 49.0,
            sentiment          = "positive",
            strong_signals     = ["community_activity", "sentiment"],
            weak_signals       = ["upload_consistency"],
        )


# ===========================================================================
# 13. Growth prediction model (Feature 13)
# ===========================================================================

class GrowthPredictionModel(BaseModel):
    growth_pct_7d:     float  = 0.0
    momentum_label:    str    = "stable"      # accelerating | stable | declining
    best_topic:        str    = ""
    declining_topic:   str    = ""
    best_upload_day:   str    = ""
    upload_gap_weeks:  int    = 0
    confidence:        ConfidenceScore = Field(default_factory=lambda: ConfidenceScore(confidence=65))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GrowthPredictionModel":
        return cls(
            growth_pct_7d    = float(d.get("growth_pct_7d", 0)),
            momentum_label   = str(d.get("momentum_label", "stable")),
            best_topic       = str(d.get("best_topic", "")),
            declining_topic  = str(d.get("declining_topic", "")),
            best_upload_day  = str(d.get("best_upload_day", "")),
            upload_gap_weeks = int(d.get("upload_gap_weeks", 0)),
        )

    @classmethod
    def mock(cls) -> "GrowthPredictionModel":
        return cls(
            growth_pct_7d   = 6.8,
            momentum_label  = "accelerating",
            best_topic      = "AI Agents",
            declining_topic = "Career Advice",
            best_upload_day = "Tuesday",
            upload_gap_weeks= 0,
            confidence      = ConfidenceScore(confidence=82, label="high"),
        )


# ===========================================================================
# 3. Analytics response model (Feature 3)
# ===========================================================================

class VideoAnalyticsModel(BaseModel):
    video_id:       str   = ""
    title:          str   = ""
    topic:          str   = ""
    views:          int   = 0
    resonance_score: float = 0.0
    watch_pct:      float = 0.0
    engagement_ratio: float = 0.0
    discord_msgs:   int   = 0
    spike_ratio:    float = 1.0
    sentiment:      str   = "neutral"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "VideoAnalyticsModel":
        views = int(row.get("views", 0))
        likes = int(row.get("likes", 0))
        comments = int(row.get("comments", 0))
        return cls(
            video_id         = str(row.get("video_id", "")),
            title            = str(row.get("title", "")),
            topic            = str(row.get("topic", "")),
            views            = views,
            resonance_score  = float(row.get("resonance_score", 0)),
            watch_pct        = float(row.get("watch_pct", 0)),
            engagement_ratio = round((likes + comments) / max(views, 1), 4),
            discord_msgs     = int(row.get("discord_msg_count", 0)),
            spike_ratio      = float(row.get("community_spike_ratio", 1)),
            sentiment        = str(row.get("sentiment_label", "neutral")),
        )


class AnalyticsResponse(BaseModel):
    channel_avg_resonance: float = 0.0
    top_topic:             str   = ""
    videos:                list[VideoAnalyticsModel] = Field(default_factory=list)
    trends:                list[TrendModel]          = Field(default_factory=list)
    underperformers:       list[UnderperformerModel] = Field(default_factory=list)
    audience_health:       AudienceHealthModel       = Field(default_factory=AudienceHealthModel)
    growth_forecast:       GrowthPredictionModel     = Field(default_factory=GrowthPredictionModel)
    data_points:           int   = 0

    @classmethod
    def mock(cls) -> "AnalyticsResponse":
        return cls(
            channel_avg_resonance = 65.0,
            top_topic             = "AI Agents",
            videos = [
                VideoAnalyticsModel(
                    video_id="v001", title="Building an AI Agent from Scratch",
                    topic="AI Agents", views=85000, resonance_score=91.0,
                    watch_pct=71.0, discord_msgs=187, spike_ratio=4.1,
                ),
                VideoAnalyticsModel(
                    video_id="v003", title="Career Q&A #12",
                    topic="Career Advice", views=180000, resonance_score=31.0,
                    watch_pct=22.0, discord_msgs=3, spike_ratio=0.8,
                ),
            ],
            audience_health  = AudienceHealthModel.mock(),
            growth_forecast  = GrowthPredictionModel.mock(),
            data_points      = 12,
        )


# ===========================================================================
# 4. Insight response model (Feature 4)
# ===========================================================================

class InsightCard(BaseModel):
    """A single insight card for the dashboard. (Feature 17)"""
    type:          str   = ""    # top_opportunity | underperformance | audience_health | growth
    title:         str   = ""
    summary:       str   = ""
    key_insight:   str   = ""
    signals:       list[str] = Field(default_factory=list)
    recommendation: str  = ""
    confidence:    ConfidenceScore = Field(default_factory=lambda: ConfidenceScore(confidence=70))
    from_mock:     bool  = False

    @classmethod
    def from_insight_response(cls, ir: Any, insight_type: str = "") -> "InsightCard":
        return cls(
            type        = insight_type or ir.intent,
            title       = ir.summary[:80] if ir.summary else "",
            summary     = ir.summary,
            key_insight = ir.key_insight,
            signals     = ir.signals,
            recommendation = ir.recommendation,
            confidence  = ConfidenceScore(confidence=ir.confidence, label=ir.confidence_label),
            from_mock   = ir.from_mock,
        )


class InsightResponse(BaseModel):
    """
    Full dashboard insight payload.
    Returned by GET /api/insights and related endpoints.
    """
    summary:         str   = ""
    top_insights:    list[InsightCard]         = Field(default_factory=list)
    recommendations: list[RecommendationModel] = Field(default_factory=list)
    risks:           list[str]                 = Field(default_factory=list)
    opportunities:   list[str]                 = Field(default_factory=list)
    confidence:      ConfidenceScore           = Field(default_factory=lambda: ConfidenceScore(confidence=70))
    analytics:       AnalyticsResponse | None  = None
    from_mock:       bool = False
    latency_ms:      int  = 0

    @classmethod
    def mock(cls) -> "InsightResponse":
        return cls(
            summary = "AI Agents content is your highest-leverage growth opportunity.",
            top_insights = [
                InsightCard(
                    type        = "top_opportunity",
                    title       = "AI Agents content outperforms all other topics",
                    summary     = "AI tutorials average 84 resonance — 31 pts above channel average",
                    key_insight = (
                        "AI Agent tutorials drive 3.2× more Discord messages and 68% retention — "
                        "your most consistent high-performer."
                    ),
                    signals     = ["Resonance: 84/100", "Discord spike: 3.2×", "Retention: 68%"],
                    recommendation = "Publish 2 AI Agent tutorials in the next 3 weeks",
                    confidence  = ConfidenceScore(confidence=91, label="high"),
                ),
            ],
            recommendations = [RecommendationModel.mock()],
            risks        = ["Career advice content resonance declining (-6.2 pts)"],
            opportunities= ["AI Agents momentum accelerating (+8.5 pts)"],
            confidence   = ConfidenceScore(confidence=85, label="high"),
            from_mock    = True,
        )


# ===========================================================================
# 7. Health response model (Feature 7)
# ===========================================================================

class ServiceStatus(BaseModel):
    name:    str  = ""
    status:  str  = "unknown"     # healthy | degraded | unavailable | mock
    latency_ms: int | None = None
    detail:  str  = ""


class HealthResponse(BaseModel):
    """System health payload for GET /api/health."""
    status:    str   = "healthy"   # healthy | degraded | unhealthy
    uptime_s:  float = 0.0
    version:   str   = "1.0.0"
    services:  list[ServiceStatus] = Field(default_factory=list)
    timestamp: str   = Field(default_factory=_now_iso)

    def overall_status(self) -> str:
        statuses = {s.status for s in self.services}
        if "unavailable" in statuses:
            return "degraded"
        return "healthy"

    @classmethod
    def mock(cls, uptime_s: float = 0.0) -> "HealthResponse":
        return cls(
            status   = "healthy",
            uptime_s = uptime_s,
            services = [
                ServiceStatus(name="coral",        status="mock",    detail="Mock data active"),
                ServiceStatus(name="claude",       status="healthy", latency_ms=340),
                ServiceStatus(name="youtube",      status="mock",    detail="Demo mode"),
                ServiceStatus(name="discord",      status="mock",    detail="Demo mode"),
                ServiceStatus(name="google_sheets",status="mock",    detail="Demo mode"),
            ],
        )


# ===========================================================================
# 8. Source status model (Feature 8)
# ===========================================================================

class SourceStatus(BaseModel):
    name:      str  = ""
    status:    str  = "unknown"    # healthy | mock | disconnected | error
    icon:      str  = ""
    last_sync: str  = ""
    record_count: int = 0
    detail:    str  = ""

    @classmethod
    def mock_set(cls) -> list["SourceStatus"]:
        return [
            cls(name="YouTube",       status="mock",    icon="▶", detail="Demo data active", record_count=4),
            cls(name="Discord",       status="mock",    icon="💬", detail="Demo data active", record_count=4),
            cls(name="Google Sheets", status="mock",    icon="📊", detail="Demo data active", record_count=12),
            cls(name="Coral SQL",     status="mock",    icon="🪸", detail="Multi-source JOIN active"),
            cls(name="Claude AI",     status="healthy", icon="🤖", detail="Anthropic API connected"),
        ]


# ===========================================================================
# 14. Pagination model (Feature 14)
# ===========================================================================

class PaginatedResponse(BaseModel, Generic[DataT]):
    """
    Wrapper for list endpoints that support pagination.
    Used by /api/insights/history, /api/analytics/timeline, etc.
    """
    items:    list[DataT]  = Field(default_factory=list)
    page:     int          = 1
    limit:    int          = 20
    total:    int          = 0
    has_more: bool         = False

    @model_validator(mode="after")
    def _set_has_more(self) -> "PaginatedResponse[DataT]":
        self.has_more = (self.page * self.limit) < self.total
        return self

    @classmethod
    def from_list(
        cls,
        items:  list[DataT],
        page:   int = 1,
        limit:  int = 20,
        total:  int | None = None,
    ) -> "PaginatedResponse[DataT]":
        actual_total = total if total is not None else len(items)
        start = (page - 1) * limit
        return cls(
            items    = items[start: start + limit],
            page     = page,
            limit    = limit,
            total    = actual_total,
        )


# ===========================================================================
# 15. Request metadata model (Feature 15)
# ===========================================================================

class RequestMetadata(BaseModel):
    """
    Attached to API responses as the `metadata` field.
    Gives the frontend timing, source transparency, and debug info.
    """
    latency_ms:    int   = 0
    model_used:    str   = ""
    intent:        str   = ""
    data_points:   int   = 0
    from_mock:     bool  = False
    from_cache:    bool  = False
    coral_sources: list[str] = Field(default_factory=list)
    prompt_version: str  = ""
    channel_id:    str   = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


# ===========================================================================
# Quick-access bundle — import everything from one place
# ===========================================================================

__all__ = [
    "APIResponse",
    "ErrorResponse",
    "ConfidenceScore",
    "RecommendationModel",
    "ChatResponse",
    "TrendModel",
    "UnderperformerModel",
    "AudienceHealthModel",
    "GrowthPredictionModel",
    "VideoAnalyticsModel",
    "AnalyticsResponse",
    "InsightCard",
    "InsightResponse",
    "HealthResponse",
    "ServiceStatus",
    "SourceStatus",
    "PaginatedResponse",
    "RequestMetadata",
]
