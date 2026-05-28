"""
models/insight_models.py
CreatorPulse · Creator Intelligence Data Model System

Role: Define the INTERNAL structure of AI insights, detections,
      recommendations, risks, and opportunities as they flow through
      the intelligence pipeline.

Distinction from response_models.py:
  response_models.py  — external API output shapes (what the frontend sees)
  insight_models.py   — internal brain objects (what the pipeline works with)

The pipeline builds these objects; insight_engine.py converts them to
response_models before returning to any route handler.

Used by:
  ai/insight_engine.py      ai/detectors.py
  ai/recommendations.py     scoring/resonance_score.py
  scoring/audience_health.py scoring/growth_predictor.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ===========================================================================
# Enums — shared across all insight models
# ===========================================================================

class InsightType(str, Enum):
    GROWTH_OPPORTUNITY   = "growth_opportunity"
    UNDERPERFORMANCE     = "underperformance"
    AUDIENCE_HEALTH      = "audience_health"
    RESONANCE_ANALYSIS   = "resonance_analysis"
    TREND_SHIFT          = "trend_shift"
    VIRAL_SIGNAL         = "viral_signal"
    RISK_ALERT           = "risk_alert"
    CONTENT_STRATEGY     = "content_strategy"
    GENERAL              = "general"


class InsightPriority(str, Enum):
    """Feature 15: Insight Priority System"""
    CRITICAL = "critical"   # act immediately (viral window, severe decline)
    HIGH     = "high"       # act this week
    MEDIUM   = "medium"     # act this month
    LOW      = "low"        # nice to have


class RiskSeverity(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


class MomentumDirection(str, Enum):
    RISING    = "rising"
    STABLE    = "stable"
    DECLINING = "declining"


# ===========================================================================
# Feature 16: Confidence scoring model (reused everywhere)
# ===========================================================================

@dataclass
class ConfidenceModel:
    """
    Internal confidence representation.
    Accepts 0–1 fractions; exposes pct() for display.
    """
    value:   float  = 0.0    # 0.0–1.0
    label:   str    = ""     # high | moderate | low
    reason:  str    = ""
    factors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Normalise to 0–1 if given as percentage
        if self.value > 1.0:
            self.value = self.value / 100.0
        self.value = max(0.0, min(1.0, self.value))
        if not self.label:
            self.label = (
                "high"     if self.value >= 0.80 else
                "moderate" if self.value >= 0.60 else
                "low"
            )

    def pct(self) -> float:
        """Return confidence as 0–100 float."""
        if math.isnan(self.value):
            return 0.0
        return round(self.value * 100, 1)

    @classmethod
    def from_float(cls, v: float, reason: str = "") -> "ConfidenceModel":
        return cls(value=v, reason=reason)

    @classmethod
    def high(cls, reason: str = "") -> "ConfidenceModel":
        return cls(value=0.88, reason=reason)

    @classmethod
    def moderate(cls, reason: str = "") -> "ConfidenceModel":
        return cls(value=0.70, reason=reason)

    @classmethod
    def low(cls, reason: str = "") -> "ConfidenceModel":
        return cls(value=0.45, reason=reason)


# ===========================================================================
# Feature 1: Core Insight Object
# ===========================================================================

@dataclass
class InsightObject:
    """
    The fundamental intelligence unit — produced by insight_engine.py
    and consumed by routes + frontend dashboard cards.
    (Feature 1: Insight Object Model)
    """
    title:          str
    summary:        str
    insight_type:   InsightType          = InsightType.GENERAL
    priority:       InsightPriority      = InsightPriority.MEDIUM
    confidence:     ConfidenceModel      = field(default_factory=ConfidenceModel.moderate)

    # Reasoning chain (Feature 15: AI Explanation Layer)
    reasoning:      str   = ""
    evidence:       list[str] = field(default_factory=list)   # quoted data points
    recommendation: str   = ""

    # Source tracing
    topics_involved:  list[str] = field(default_factory=list)
    videos_involved:  list[str] = field(default_factory=list)
    data_sources:     list[str] = field(default_factory=list)

    # Time sensitivity
    is_time_sensitive: bool = False    # True = viral window, act within 72h
    action_window:     str  = ""       # "72 hours" | "this week" | "this month"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title":          self.title,
            "summary":        self.summary,
            "type":           self.insight_type.value,
            "priority":       self.priority.value,
            "confidence":     self.confidence.pct(),
            "confidence_label": self.confidence.label,
            "reasoning":      self.reasoning,
            "evidence":       self.evidence,
            "recommendation": self.recommendation,
            "topics":         self.topics_involved,
            "time_sensitive": self.is_time_sensitive,
            "action_window":  self.action_window,
        }


# ===========================================================================
# Feature 2: Recommendation Model (internal)
# ===========================================================================

@dataclass
class RecommendationInsight:
    """
    Internal recommendation object produced by recommendations.py.
    Richer than the API-facing RecommendationModel in response_models.py.
    (Feature 2)
    """
    title:           str
    action:          str
    description:     str            = ""
    reason:          str            = ""
    priority:        InsightPriority= InsightPriority.MEDIUM
    confidence:      ConfidenceModel= field(default_factory=ConfidenceModel.moderate)
    expected_impact: str            = ""

    category:        str            = ""     # content_strategy | growth | retention | …
    topic:           str | None     = None
    related_videos:  list[str]      = field(default_factory=list)
    supporting_data: list[str]      = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title":           self.title,
            "action":          self.action,
            "description":     self.description,
            "reason":          self.reason,
            "priority":        self.priority.value,
            "confidence":      self.confidence.pct(),
            "expected_impact": self.expected_impact,
            "category":        self.category,
            "topic":           self.topic,
        }


# ===========================================================================
# Feature 3: Risk Signal Model
# ===========================================================================

@dataclass
class RiskSignal:
    """
    Represents a detected creator growth risk.
    (Feature 3: Risk Signal Model)
    """
    risk_type:  str              # audience_fatigue | retention_decline | topic_decay | …
    severity:   RiskSeverity     = RiskSeverity.MEDIUM
    reason:     str              = ""
    confidence: ConfidenceModel  = field(default_factory=ConfidenceModel.moderate)
    mitigation: str              = ""
    metrics:    dict[str, Any]   = field(default_factory=dict)

    # Human-readable variants
    @property
    def label(self) -> str:
        return self.risk_type.replace("_", " ").title()

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_type":  self.risk_type,
            "label":      self.label,
            "severity":   self.severity.value,
            "reason":     self.reason,
            "confidence": self.confidence.pct(),
            "mitigation": self.mitigation,
        }

    # ── Common factory constructors ────────────────────────────────────────
    @classmethod
    def audience_fatigue(cls, reason: str = "") -> "RiskSignal":
        return cls(
            risk_type  = "audience_fatigue",
            severity   = RiskSeverity.MEDIUM,
            reason     = reason or "Engagement CV above fatigue threshold",
            mitigation = "Introduce a new content format or interactive series",
            confidence = ConfidenceModel.moderate(),
        )

    @classmethod
    def retention_decline(cls, watch_pct: float, reason: str = "") -> "RiskSignal":
        return cls(
            risk_type  = "retention_decline",
            severity   = RiskSeverity.HIGH if watch_pct < 25 else RiskSeverity.MEDIUM,
            reason     = reason or f"Average watch % dropped to {watch_pct:.0f}%",
            mitigation = "Tighten the first 30 seconds across next 3 uploads",
            confidence = ConfidenceModel.moderate(),
            metrics    = {"watch_pct": watch_pct},
        )

    @classmethod
    def topic_decay(cls, topic: str, delta: float) -> "RiskSignal":
        return cls(
            risk_type  = "topic_decay",
            severity   = RiskSeverity.HIGH if delta < -8 else RiskSeverity.MEDIUM,
            reason     = f"'{topic}' resonance delta: {delta:+.1f} pts this period",
            mitigation = f"Angle '{topic}' content toward your dominant niche or reduce frequency",
            confidence = ConfidenceModel.moderate(),
            metrics    = {"topic": topic, "delta": delta},
        )


# ===========================================================================
# Feature 4: Opportunity Signal Model
# ===========================================================================

@dataclass
class OpportunitySignal:
    """
    Represents a detected creator growth opportunity.
    (Feature 4: Opportunity Signal Model)
    """
    opportunity_type: str               # viral_window | topic_growth | community_spike | …
    impact:           str    = "medium" # high | medium | low
    confidence:       ConfidenceModel   = field(default_factory=ConfidenceModel.moderate)
    signals:          list[str]         = field(default_factory=list)
    action:           str    = ""
    topic:            str | None = None
    time_sensitive:   bool   = False
    window_hours:     int    = 0        # 0 = not time-gated

    @property
    def label(self) -> str:
        return self.opportunity_type.replace("_", " ").title()

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_type": self.opportunity_type,
            "label":            self.label,
            "impact":           self.impact,
            "confidence":       self.confidence.pct(),
            "signals":          self.signals,
            "action":           self.action,
            "topic":            self.topic,
            "time_sensitive":   self.time_sensitive,
            "window_hours":     self.window_hours,
        }

    @classmethod
    def viral_window(cls, title: str, spike_ratio: float, resonance: float) -> "OpportunitySignal":
        return cls(
            opportunity_type = "viral_window",
            impact           = "high",
            confidence       = ConfidenceModel.high("Resonance + spike both above threshold"),
            signals          = [
                f"Community spike: {spike_ratio:.1f}× baseline",
                f"Resonance: {resonance:.0f}/100",
                "Action window: 72 hours",
            ],
            action         = f"Publish a follow-up to '{title[:40]}' immediately",
            time_sensitive = True,
            window_hours   = 72,
        )

    @classmethod
    def topic_growth(cls, topic: str, delta: float, resonance: float) -> "OpportunitySignal":
        return cls(
            opportunity_type = "topic_growth",
            impact           = "high" if delta > 6 else "medium",
            confidence       = ConfidenceModel.from_float(min(0.5 + delta / 20, 0.92)),
            signals          = [
                f"Resonance delta: {delta:+.1f} pts",
                f"Avg resonance: {resonance:.0f}/100",
            ],
            action = f"Increase '{topic}' upload frequency over the next 4 weeks",
            topic  = topic,
        )


# ===========================================================================
# Feature 5: Content Insight Model
# ===========================================================================

@dataclass
class ContentInsight:
    """
    Per-topic or per-video content performance intelligence.
    (Feature 5: Content Insight Model)
    """
    topic:               str
    resonance:           float  = 0.0
    retention_pct:       float  = 0.0
    discord_msgs_avg:    float  = 0.0
    spike_ratio:         float  = 1.0
    sentiment:           str    = "neutral"
    performance_summary: str    = ""
    strengths:           list[str] = field(default_factory=list)
    weaknesses:          list[str] = field(default_factory=list)
    video_count:         int    = 0

    def performance_tier(self) -> str:
        if self.resonance >= 75:
            return "top_performer"
        if self.resonance >= 50:
            return "average"
        return "underperformer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic":           self.topic,
            "resonance":       self.resonance,
            "retention_pct":   self.retention_pct,
            "discord_msgs_avg":self.discord_msgs_avg,
            "spike_ratio":     self.spike_ratio,
            "sentiment":       self.sentiment,
            "summary":         self.performance_summary,
            "tier":            self.performance_tier(),
            "strengths":       self.strengths,
            "weaknesses":      self.weaknesses,
        }

    @classmethod
    def from_rows(cls, topic: str, rows: list[dict[str, Any]]) -> "ContentInsight":
        if not rows:
            return cls(topic=topic)
        resonances  = [float(r.get("resonance_score", 0)) for r in rows]
        retentions  = [float(r.get("watch_pct", 0))       for r in rows]
        discords    = [float(r.get("discord_msg_count", 0))for r in rows]
        spikes      = [float(r.get("community_spike_ratio",1)) for r in rows]
        avg_res     = sum(resonances)  / len(resonances)
        avg_ret     = sum(retentions)  / len(retentions)
        avg_dis     = sum(discords)    / len(discords)
        avg_spike   = sum(spikes)      / len(spikes)
        strengths: list[str] = []
        weaknesses: list[str]= []
        if avg_ret  >= 60: strengths.append(f"strong retention ({avg_ret:.0f}%)")
        if avg_ret  <  35: weaknesses.append(f"weak retention ({avg_ret:.0f}%)")
        if avg_dis  >= 80: strengths.append(f"active community ({avg_dis:.0f} msgs/video)")
        if avg_dis  <  5:  weaknesses.append("community silence")
        if avg_spike >= 3: strengths.append(f"community spike pattern ({avg_spike:.1f}×)")
        summary = (
            f"{topic} averages {avg_res:.0f} resonance across {len(rows)} videos "
            f"with {avg_ret:.0f}% retention and {avg_dis:.0f} Discord msgs/video."
        )
        return cls(
            topic               = topic,
            resonance           = round(avg_res, 1),
            retention_pct       = round(avg_ret, 1),
            discord_msgs_avg    = round(avg_dis, 1),
            spike_ratio         = round(avg_spike, 2),
            performance_summary = summary,
            strengths           = strengths,
            weaknesses          = weaknesses,
            video_count         = len(rows),
        )


# ===========================================================================
# Feature 6: Audience Insight Model
# ===========================================================================

@dataclass
class AudienceInsight:
    """
    Audience intelligence synthesised by audience_health.py.
    (Feature 6: Audience Insight Model)
    """
    health_score:        float  = 0.0
    loyalty:             str    = "unknown"   # high | medium | low
    engagement_quality:  str    = "unknown"
    community_health:    float  = 0.0
    sentiment:           str    = "neutral"
    flag_passive:        bool   = False
    flag_burnout:        bool   = False
    strong_signals:      list[str] = field(default_factory=list)
    weak_signals:        list[str] = field(default_factory=list)
    insight_summary:     str    = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "health_score":       self.health_score,
            "loyalty":            self.loyalty,
            "engagement_quality": self.engagement_quality,
            "community_health":   self.community_health,
            "sentiment":          self.sentiment,
            "passive_audience":   self.flag_passive,
            "burnout_signal":     self.flag_burnout,
            "strong_signals":     self.strong_signals,
            "weak_signals":       self.weak_signals,
            "summary":            self.insight_summary,
        }

    @classmethod
    def from_health_dict(cls, d: dict[str, Any]) -> "AudienceInsight":
        score = float(d.get("health_score", 0))
        return cls(
            health_score       = score,
            loyalty            = "high" if score >= 70 else "medium" if score >= 50 else "low",
            engagement_quality = d.get("engagement_quality", "unknown"),
            community_health   = float(d.get("community_loyalty_index", 0)),
            sentiment          = d.get("overall_sentiment", "neutral"),
            flag_passive       = bool(d.get("flag_passive_audience", False)),
            flag_burnout       = bool(d.get("flag_burnout", False)),
            strong_signals     = list(d.get("strong_signals", [])),
            weak_signals       = list(d.get("weak_signals",   [])),
            insight_summary    = (
                f"Audience health: {score:.0f}/100 ({d.get('health_label','unknown')}). "
                + (f"Strong: {', '.join(d.get('strong_signals',[])[:2])}. " if d.get("strong_signals") else "")
                + (f"Weak: {', '.join(d.get('weak_signals',[])[:2])}." if d.get("weak_signals")   else "")
            ),
        )


# ===========================================================================
# Feature 7: Growth Prediction Model (internal)
# ===========================================================================

@dataclass
class GrowthPredictionInsight:
    """
    Internal growth forecast produced by growth_predictor.py.
    (Feature 7: Growth Prediction Model)
    """
    growth_pct_7d:    float              = 0.0
    momentum_label:   MomentumDirection  = MomentumDirection.STABLE
    confidence:       ConfidenceModel    = field(default_factory=ConfidenceModel.moderate)
    drivers:          list[str]          = field(default_factory=list)
    risks:            list[str]          = field(default_factory=list)
    best_topic:       str                = ""
    declining_topic:  str                = ""
    timeframe_days:   int                = 7
    upload_rec:       str                = ""

    @property
    def growth_label(self) -> str:
        if self.growth_pct_7d >= 5:
            return "strong growth"
        if self.growth_pct_7d >= 1:
            return "moderate growth"
        if self.growth_pct_7d >= -1:
            return "flat"
        return "declining"

    def to_dict(self) -> dict[str, Any]:
        return {
            "growth_pct_7d":   self.growth_pct_7d,
            "growth_label":    self.growth_label,
            "momentum":        self.momentum_label.value,
            "confidence":      self.confidence.pct(),
            "drivers":         self.drivers,
            "risks":           self.risks,
            "best_topic":      self.best_topic,
            "declining_topic": self.declining_topic,
            "timeframe_days":  self.timeframe_days,
            "upload_rec":      self.upload_rec,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GrowthPredictionInsight":
        momentum_raw = d.get("momentum_label", "stable")
        try:
            momentum = MomentumDirection(momentum_raw)
        except ValueError:
            momentum = MomentumDirection.STABLE
        drivers = []
        if d.get("best_topic"):
            drivers.append(f"Strong topic momentum: {d['best_topic']}")
        if float(d.get("growth_pct_7d", 0)) > 0:
            drivers.append(f"Positive resonance trend: {d.get('growth_pct_7d',0):+.1f}% over 7 days")
        risks = []
        if d.get("declining_topic"):
            risks.append(f"'{d['declining_topic']}' losing resonance")
        if d.get("upload_gap_weeks", 0) >= 2:
            risks.append("Upload gaps suppressing distribution")
        return cls(
            growth_pct_7d   = float(d.get("growth_pct_7d", 0)),
            momentum_label  = momentum,
            drivers         = drivers,
            risks           = risks,
            best_topic      = str(d.get("best_topic", "")),
            declining_topic = str(d.get("declining_topic", "")),
            upload_rec      = str(d.get("upload_simulation", {}).get("recommended", "")),
        )


# ===========================================================================
# Feature 8: Resonance Insight Model
# ===========================================================================

@dataclass
class ResonanceInsight:
    """
    Per-video resonance intelligence from resonance_score.py.
    (Feature 8: Resonance Insight Model)
    """
    video_id:     str
    title:        str
    score:        float              = 0.0
    channel_avg:  float              = 0.0
    drivers:      list[str]          = field(default_factory=list)
    strengths:    list[str]          = field(default_factory=list)
    weaknesses:   list[str]          = field(default_factory=list)
    explanation:  str                = ""
    is_outlier:   bool               = False   # significantly above/below average

    @property
    def delta_from_avg(self) -> float:
        return round(self.score - self.channel_avg, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id":    self.video_id,
            "title":       self.title,
            "score":       self.score,
            "channel_avg": self.channel_avg,
            "delta":       self.delta_from_avg,
            "drivers":     self.drivers,
            "strengths":   self.strengths,
            "weaknesses":  self.weaknesses,
            "explanation": self.explanation,
            "is_outlier":  self.is_outlier,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any], channel_avg: float = 0.0) -> "ResonanceInsight":
        score = float(row.get("resonance_score", 0))
        drivers, strengths, weaknesses = [], [], []
        watch_pct  = float(row.get("watch_pct", 0))
        disc_msgs  = int(row.get("discord_msg_count", 0))
        spike      = float(row.get("community_spike_ratio", 1.0))
        sent       = float(row.get("sentiment_score", 0))
        if watch_pct >= 60:  strengths.append(f"strong retention ({watch_pct:.0f}%)")
        if watch_pct <  35:  weaknesses.append(f"weak retention ({watch_pct:.0f}%)")
        if disc_msgs >= 80:  strengths.append(f"high community discussion ({disc_msgs} msgs)")
        if disc_msgs <  5:   weaknesses.append("community silence")
        if spike >= 3.0:     drivers.append(f"community spike ({spike:.1f}×)")
        if sent  > 0.4:      drivers.append("positive audience sentiment")
        elif sent < -0.3:    weaknesses.append("negative sentiment signal")
        explanation = (
            f"'{row.get('title','')}' scores {score:.0f}/100 resonance "
            f"({'above' if score >= channel_avg else 'below'} channel avg of {channel_avg:.0f}). "
            + (f"Strengths: {', '.join(strengths)}. " if strengths else "")
            + (f"Weaknesses: {', '.join(weaknesses)}." if weaknesses else "")
        )
        return cls(
            video_id    = str(row.get("video_id", "")),
            title       = str(row.get("title", "")),
            score       = round(score, 1),
            channel_avg = round(channel_avg, 1),
            drivers     = drivers,
            strengths   = strengths,
            weaknesses  = weaknesses,
            explanation = explanation,
            is_outlier  = abs(score - channel_avg) > 20,
        )


# ===========================================================================
# Feature 9: Underperformance Insight Model
# ===========================================================================

@dataclass
class UnderperformanceInsight:
    """
    Diagnosis object for a single underperforming video.
    (Feature 9: Underperformance Insight Model)
    """
    video_id:    str
    title:       str
    problem:     str    = ""
    cause:       str    = ""
    fix:         str    = ""
    severity:    RiskSeverity   = RiskSeverity.MEDIUM
    metrics:     dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title":    self.title,
            "problem":  self.problem,
            "cause":    self.cause,
            "fix":      self.fix,
            "severity": self.severity.value,
            "metrics":  self.metrics,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "UnderperformanceInsight":
        diagnosis = str(row.get("primary_diagnosis", "low_retention"))
        problem_map = {
            "low_retention":          "Watch time drops off early",
            "ctr_retention_mismatch": "High clicks but viewers leave quickly",
            "community_silence":      "No community discussion after upload",
            "false_popularity":       "High views masking weak engagement",
            "weak_engagement":        "Viewers watch but don't interact",
        }
        fix_map = {
            "low_retention":          "Rewrite the first 30 seconds — lead with the core value immediately",
            "ctr_retention_mismatch": "Deliver the thumbnail's implied promise within the first 60 seconds",
            "community_silence":      "End the video with a direct question and pin it in Discord",
            "false_popularity":       "Reframe title to attract viewers who will actually stay",
            "weak_engagement":        "Add 1–2 mid-video engagement triggers (polls, comment questions)",
        }
        watch_pct = float(row.get("watch_pct", 0))
        return cls(
            video_id = str(row.get("video_id", "")),
            title    = str(row.get("title", "")),
            problem  = problem_map.get(diagnosis, "Multiple weak signals"),
            cause    = diagnosis.replace("_", " ").title(),
            fix      = fix_map.get(diagnosis, "Review hook and pacing"),
            severity = RiskSeverity.HIGH if watch_pct < 25 else RiskSeverity.MEDIUM,
            metrics  = {
                "resonance_score": float(row.get("resonance_score", 0)),
                "watch_pct":       watch_pct,
                "discord_msgs":    int(row.get("discord_msg_count", 0)),
            },
        )


# ===========================================================================
# Feature 10: Trend Insight Model
# ===========================================================================

@dataclass
class TrendInsight:
    """
    Topic trend intelligence from trends.sql + growth_predictor.py.
    (Feature 10: Trend Insight Model)
    """
    topic:       str
    direction:   MomentumDirection  = MomentumDirection.STABLE
    momentum:    str                = ""     # "strong rising" | "slight decline" etc.
    velocity:    float              = 0.0   # resonance pts per period
    resonance:   float              = 0.0
    confidence:  ConfidenceModel    = field(default_factory=ConfidenceModel.moderate)
    explanation: str                = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic":       self.topic,
            "direction":   self.direction.value,
            "momentum":    self.momentum,
            "velocity":    self.velocity,
            "resonance":   self.resonance,
            "confidence":  self.confidence.pct(),
            "explanation": self.explanation,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TrendInsight":
        delta = float(row.get("resonance_delta", 0))
        if delta > 6:
            direction, momentum = MomentumDirection.RISING,   "strong rising"
        elif delta > 2:
            direction, momentum = MomentumDirection.RISING,   "slight rising"
        elif delta < -6:
            direction, momentum = MomentumDirection.DECLINING, "strong declining"
        elif delta < -2:
            direction, momentum = MomentumDirection.DECLINING, "slight declining"
        else:
            direction, momentum = MomentumDirection.STABLE,   "stable"
        topic = str(row.get("topic", ""))
        explanation = (
            f"'{topic}' resonance is {momentum} with a {delta:+.1f} pt delta this period."
        )
        return cls(
            topic       = topic,
            direction   = direction,
            momentum    = momentum,
            velocity    = round(delta, 2),
            resonance   = float(row.get("resonance_score", 0)),
            confidence  = ConfidenceModel.from_float(0.70),
            explanation = explanation,
        )


# ===========================================================================
# Feature 11: Detector Result Model
# ===========================================================================

@dataclass
class DetectorResult:
    """
    Structured output from a single detector check.
    Used by detectors.py to build the DetectionReport.
    (Feature 11: Detector Result Model)
    """
    detector_name: str
    triggered:     bool              = False
    reason:        str               = ""
    confidence:    ConfidenceModel   = field(default_factory=ConfidenceModel.moderate)
    evidence:      list[str]         = field(default_factory=list)
    affected:      list[str]         = field(default_factory=list)   # video titles or topics

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector":   self.detector_name,
            "triggered":  self.triggered,
            "reason":     self.reason,
            "confidence": self.confidence.pct(),
            "evidence":   self.evidence,
            "affected":   self.affected,
        }


# ===========================================================================
# Feature 12: AI Context Model
# ===========================================================================

@dataclass
class AIContextModel:
    """
    Structured context assembled for injection into the Claude prompt.
    Prevents raw JSON being passed to the LLM.
    (Feature 12: AI Context Model)
    """
    channel_avg_resonance: float  = 0.0
    top_topic:             str    = ""
    data_points:           int    = 0

    content_insights:     list[ContentInsight]         = field(default_factory=list)
    resonance_insights:   list[ResonanceInsight]        = field(default_factory=list)
    trend_insights:       list[TrendInsight]            = field(default_factory=list)
    underperformance:     list[UnderperformanceInsight] = field(default_factory=list)

    audience_insight:     AudienceInsight | None          = None
    growth_insight:       GrowthPredictionInsight | None  = None
    risk_signals:         list[RiskSignal]                = field(default_factory=list)
    opportunities:        list[OpportunitySignal]         = field(default_factory=list)
    recommendations:      list[RecommendationInsight]     = field(default_factory=list)

    def to_prompt_block(self) -> str:
        """Render as a structured markdown block for Claude."""
        lines: list[str] = ["## CreatorPulse Intelligence Context\n"]
        lines.append(f"**Channel avg resonance:** {self.channel_avg_resonance:.0f}/100")
        lines.append(f"**Top topic:** {self.top_topic}  |  **Data points:** {self.data_points}\n")
        if self.content_insights:
            lines.append("### Content by Topic")
            for ci in sorted(self.content_insights, key=lambda x: -x.resonance)[:5]:
                lines.append(f"- **{ci.topic}**: {ci.resonance:.0f} resonance | {ci.retention_pct:.0f}% retention | {ci.discord_msgs_avg:.0f} discord msgs/vid")
        if self.risk_signals:
            lines.append("\n### Risk Signals")
            for r in self.risk_signals[:3]:
                lines.append(f"- [{r.severity.value.upper()}] {r.label}: {r.reason}")
        if self.opportunities:
            lines.append("\n### Opportunities")
            for o in self.opportunities[:3]:
                lines.append(f"- [{o.impact.upper()}] {o.label}: {o.action}")
        if self.growth_insight:
            g = self.growth_insight
            lines.append(f"\n### Growth Forecast\n- Momentum: {g.momentum_label.value} | 7d: {g.growth_pct_7d:+.1f}%")
        if self.audience_insight:
            a = self.audience_insight
            lines.append(f"\n### Audience Health\n- Score: {a.health_score:.0f}/100 | Sentiment: {a.sentiment}")
        return "\n".join(lines)


# ===========================================================================
# Feature 13: Chat Insight Model
# ===========================================================================

@dataclass
class ChatInsight:
    """
    Conversational intelligence object for routes/chat.py.
    (Feature 13: Chat Insight Model)
    """
    question:        str
    answer:          str               = ""
    intent:          str               = "general_chat"
    confidence:      ConfidenceModel   = field(default_factory=ConfidenceModel.moderate)
    recommendations: list[RecommendationInsight] = field(default_factory=list)
    follow_up_prompts: list[str]                 = field(default_factory=list)
    signals:         list[str]                   = field(default_factory=list)
    from_mock:       bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "question":         self.question,
            "answer":           self.answer,
            "intent":           self.intent,
            "confidence":       self.confidence.pct(),
            "recommendations":  [r.to_dict() for r in self.recommendations],
            "follow_up_prompts":self.follow_up_prompts,
            "signals":          self.signals,
            "from_mock":        self.from_mock,
        }


# ===========================================================================
# Feature 14: Dashboard Insight Model
# ===========================================================================

@dataclass
class DashboardInsight:
    """
    Frontend card object — one per dashboard widget panel.
    (Feature 14: Dashboard Insight Model)
    """
    card_type:   str    # top_opportunity | growth_risk | best_topic | audience_warning | viral_signal
    title:       str    = ""
    value:       str    = ""   # primary metric display ("84/100", "+6.8%", "AI Agents")
    subtitle:    str    = ""
    body:        str    = ""
    cta:         str    = ""   # call-to-action text
    priority:    InsightPriority       = InsightPriority.MEDIUM
    confidence:  ConfidenceModel       = field(default_factory=ConfidenceModel.moderate)
    badge:       str    = ""   # emoji badge ("🔥", "⚠", "📈")
    data:        dict[str, Any]        = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_type":  self.card_type,
            "title":      self.title,
            "value":      self.value,
            "subtitle":   self.subtitle,
            "body":       self.body,
            "cta":        self.cta,
            "priority":   self.priority.value,
            "confidence": self.confidence.pct(),
            "badge":      self.badge,
            "data":       self.data,
        }

    # ── Factory constructors ───────────────────────────────────────────────
    @classmethod
    def top_opportunity(cls, topic: str, resonance: float, delta: float) -> "DashboardInsight":
        return cls(
            card_type  = "top_opportunity",
            title      = "Top Growth Opportunity",
            value      = f"{resonance:.0f}/100",
            subtitle   = topic,
            body       = f"'{topic}' outperforms your channel average with a {delta:+.1f} pt trend delta.",
            cta        = f"Create more {topic} content",
            priority   = InsightPriority.HIGH,
            confidence = ConfidenceModel.high(),
            badge      = "📈",
        )

    @classmethod
    def growth_risk(cls, risk_label: str, detail: str) -> "DashboardInsight":
        return cls(
            card_type  = "growth_risk",
            title      = "Growth Risk",
            value      = risk_label.replace("_", " ").title(),
            body       = detail,
            priority   = InsightPriority.HIGH,
            confidence = ConfidenceModel.moderate(),
            badge      = "⚠",
        )

    @classmethod
    def viral_signal(cls, title: str, spike_ratio: float) -> "DashboardInsight":
        return cls(
            card_type  = "viral_signal",
            title      = "Viral Opportunity",
            value      = f"{spike_ratio:.1f}× spike",
            subtitle   = title[:40],
            body       = "Community activity surged. Publish follow-up within 72 hours.",
            cta        = "Create follow-up now",
            priority   = InsightPriority.CRITICAL,
            confidence = ConfidenceModel.high("Spike ratio + resonance both above threshold"),
            badge      = "🔥",
        )

    @classmethod
    def mock_set(cls) -> list["DashboardInsight"]:
        return [
            cls.top_opportunity("AI Agents", 84.0, 8.5),
            cls.viral_signal("Building an AI Agent from Scratch", 4.1),
            cls.growth_risk("topic_decay", "Career Advice resonance declining (-6.2 pts)"),
            cls(
                card_type = "audience_warning",
                title     = "Audience Health",
                value     = "73/100",
                subtitle  = "Healthy",
                body      = "Audience is engaged. Upload consistency is the main weak signal.",
                badge     = "👥",
                priority  = InsightPriority.LOW,
                confidence= ConfidenceModel.moderate(),
            ),
        ]


# ===========================================================================
# Module exports
# ===========================================================================

__all__ = [
    "InsightType", "InsightPriority", "RiskSeverity", "MomentumDirection",
    "ConfidenceModel",
    "InsightObject",
    "RecommendationInsight",
    "RiskSignal",
    "OpportunitySignal",
    "ContentInsight",
    "AudienceInsight",
    "GrowthPredictionInsight",
    "ResonanceInsight",
    "UnderperformanceInsight",
    "TrendInsight",
    "DetectorResult",
    "AIContextModel",
    "ChatInsight",
    "DashboardInsight",
]
