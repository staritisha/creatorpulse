"""
ai/detectors.py
CreatorPulse · AI Pattern Detection Engine

Role: The intelligence layer that runs BEFORE Claude receives any data.
      Detects patterns, anomalies, opportunities, and risks from raw Coral
      query rows — then returns a structured DetectionReport that
      insight_engine.py injects into the prompt context and uses to route
      to the correct template in prompts.py.

Why this matters: Claude sees clean, labelled signals instead of raw
      metric chaos, making every AI response faster, smarter, and more
      specific to the creator's actual situation.

Used by:
  ai/insight_engine.py   — pre-LLM context enrichment + intent routing
  ai/prompts.py          — intent classification cross-check
  scoring/resonance_score.py — sentiment_score and spike_ratio inputs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Threshold constants — Rule-based detection engine (Feature 17)
# All thresholds in one place; tune here during the hackathon without
# touching detection logic.
# ---------------------------------------------------------------------------

# Retention
RETENTION_STRONG:    float = 60.0
RETENTION_WEAK:      float = 40.0
RETENTION_POOR:      float = 25.0

# Resonance score
RESONANCE_HIGH:      float = 75.0
RESONANCE_LOW:       float = 45.0

# Discord activity
DISCORD_STRONG_MSGS:  int   = 80
DISCORD_WEAK_MSGS:    int   = 5
DISCORD_SPIKE_RATIO:  float = 3.0   # peak/baseline ≥ 3× = spike

# Engagement ratio (likes + comments) / views
ENGAGEMENT_STRONG:   float = 0.05
ENGAGEMENT_WEAK:     float = 0.01

# Upload consistency
UPLOAD_GAP_WARN:     int   = 2      # weeks of gap before warning
UPLOAD_GAP_SEVERE:   int   = 4

# Trend deltas
TREND_RISING:        float =  3.0
TREND_DECLINING:     float = -3.0

# Audience fatigue: engagement CV above this = inconsistent / fatiguing
FATIGUE_CV_THRESHOLD: float = 0.6

# Viral velocity: (delta / current) ≥ this = breakout potential
VIRAL_VELOCITY:      float = 0.08

# Passive audience: engagement below weak + discord below weak floor
PASSIVE_VIEWS_FLOOR: int   = 10_000

# Sentiment proxies (keyword-based, Feature 8)
POSITIVE_KEYWORDS = {
    "amazing", "love", "great", "excellent", "helpful", "brilliant",
    "awesome", "best", "finally", "clear", "perfect", "thank",
}
NEGATIVE_KEYWORDS = {
    "bad", "boring", "slow", "rushed", "confusing", "unclear",
    "disappoint", "waste", "stop", "worst", "hate", "wrong", "hard to follow",
}
CONFUSED_KEYWORDS = {
    "confused", "don't understand", "lost", "what do you mean",
    "unclear", "explain", "huh", "?", "help",
}


# ===========================================================================
# Detection result structures
# ===========================================================================

@dataclass
class VideoSignal:
    """Detected signals for a single video."""
    video_id:   str
    title:      str
    topic:      str

    # Pattern flags
    is_high_resonance:           bool  = False   # Feature 3
    is_underperformer:           bool  = False   # Feature 2
    is_false_popularity:         bool  = False   # underperformer sub-type
    is_viral_candidate:          bool  = False   # Feature 12
    has_community_spike:         bool  = False   # Feature 7
    has_weak_retention:          bool  = False
    has_strong_retention:        bool  = False
    has_passive_audience:        bool  = False   # Feature 9
    has_weak_engagement:         bool  = False

    # Sentiment (Feature 8)
    sentiment_label:  str   = "neutral"   # positive | negative | confused | excited | neutral
    sentiment_score:  float = 0.0         # -1.0 → +1.0

    # Numeric signals
    resonance_score:     float = 0.0
    watch_pct:           float = 0.0
    discord_msg_count:   int   = 0
    spike_ratio:         float = 1.0
    engagement_ratio:    float = 0.0

    # Human-readable reason string (injected into Claude context)
    signal_summary: str = ""


@dataclass
class ChannelSignal:
    """Detected channel-level patterns aggregated across all videos."""

    # Dominant topic (Feature 11)
    dominant_topic:     str   = ""
    dominant_topic_score: float = 0.0
    emerging_topic:     str   = ""      # fastest-rising (Feature 6)
    declining_topic:    str   = ""      # fastest-falling (Feature 6)

    # Audience patterns (Features 5, 9, 13)
    audience_is_passive:     bool  = False
    audience_fatigue_flag:   bool  = False
    audience_health_label:   str   = "unknown"

    # Upload consistency (Feature 10)
    upload_gap_weeks:        int   = 0
    upload_consistency_flag: str   = "ok"   # ok | warning | severe

    # Growth signals (Features 4, 14)
    growth_opportunity_topics: list[str]  = field(default_factory=list)
    risk_signals:              list[str]  = field(default_factory=list)
    stagnation_risk:           bool       = False

    # Community (Feature 7)
    channel_spike_flag:        bool  = False
    avg_discord_msgs:          float = 0.0

    # Sentiment trend (Feature 8)
    overall_sentiment:    str   = "neutral"
    sentiment_score_avg:  float = 0.0


@dataclass
class DetectionReport:
    """
    Full detection output passed to insight_engine.py.
    Contains:
      - Resolved user intent (for prompt routing)
      - Per-video signals
      - Channel-level signals
      - Ordered recommendation triggers
      - Flat context string for Claude prompt injection
    """
    # Intent (Feature 1)
    detected_intent:     str   = "general_chat"
    intent_confidence:   float = 0.0    # 0–1

    # Signals
    video_signals:   list[VideoSignal]  = field(default_factory=list)
    channel_signal:  ChannelSignal      = field(default_factory=ChannelSignal)

    # Ordered triggers for prompts.py / recommendations.py (Feature 15)
    recommendation_triggers: list[str] = field(default_factory=list)

    # Pre-built context snippet for Claude (Feature 16)
    detector_context_block: str = ""

    # Top-level flags for quick checks
    has_underperformers: bool = False
    has_high_performers: bool = False
    has_viral_candidates: bool = False
    has_growth_opportunities: bool = False
    has_risk_signals: bool = False


# ===========================================================================
# Keyword-based sentiment scorer (Feature 8)
# ===========================================================================

def _score_sentiment_from_text(text: str) -> tuple[str, float]:
    """
    Lightweight keyword sentiment scorer for Discord message content.
    Returns (label, score) where score is -1.0 → +1.0.
    """
    if not text:
        return "neutral", 0.0
    lower = text.lower()

    pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in lower)
    neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in lower)
    con_hits = sum(1 for kw in CONFUSED_KEYWORDS if kw in lower)

    total = pos_hits + neg_hits + con_hits
    if total == 0:
        return "neutral", 0.0

    score = (pos_hits - neg_hits) / total
    if con_hits > neg_hits and con_hits > pos_hits:
        return "confused", round(score * 0.5, 2)
    if score > 0.4:
        return "positive" if score < 0.8 else "excited", round(score, 2)
    if score < -0.4:
        return "negative", round(score, 2)
    return "neutral", round(score, 2)


def score_sentiment_from_messages(messages: list[str]) -> tuple[str, float]:
    """
    Aggregate sentiment across a list of Discord message strings.
    Returns (dominant_label, avg_score).  (Feature 8)
    """
    if not messages:
        return "neutral", 0.0
    scores  = [_score_sentiment_from_text(m) for m in messages]
    avg     = sum(s for _, s in scores) / len(scores)
    counts: dict[str, int] = {}
    for label, _ in scores:
        counts[label] = counts.get(label, 0) + 1
    dominant = max(counts, key=lambda l: counts[l])
    return dominant, round(avg, 3)


# ===========================================================================
# Per-video signal detection (Features 2, 3, 7, 9, 12, 16, 17)
# ===========================================================================

def detect_video_signals(row: dict[str, Any]) -> VideoSignal:
    """
    Run all rule-based detectors on a single Coral query result row.
    Column names match the output of coral/queries/resonance.sql.
    """
    video_id     = str(row.get("video_id", ""))
    title        = str(row.get("title", ""))
    topic        = str(row.get("topic", ""))
    resonance    = float(row.get("resonance_score", 0))
    watch_pct    = float(row.get("watch_pct", 0))
    views        = int(row.get("views", 0))
    likes        = int(row.get("likes", 0))
    comments     = int(row.get("comments", 0))
    discord_msgs = int(row.get("discord_msg_count", 0))
    spike_ratio  = float(row.get("community_spike_ratio", 1.0))

    eng_ratio = (likes + comments) / max(views, 1)

    # ── Rule-based flags (Feature 17) ─────────────────────────────────────

    is_high_resonance = resonance >= RESONANCE_HIGH
    is_underperformer = (
        resonance < RESONANCE_LOW
        or watch_pct < RETENTION_WEAK
        or (views > PASSIVE_VIEWS_FLOOR and discord_msgs < DISCORD_WEAK_MSGS)
    )
    is_false_popularity = (
        views > PASSIVE_VIEWS_FLOOR * 5
        and watch_pct < RETENTION_POOR
        and discord_msgs < DISCORD_WEAK_MSGS
    )
    has_community_spike  = spike_ratio >= DISCORD_SPIKE_RATIO   # Feature 7
    has_weak_retention   = watch_pct < RETENTION_WEAK
    has_strong_retention = watch_pct >= RETENTION_STRONG
    has_passive_audience = (                                      # Feature 9
        views > PASSIVE_VIEWS_FLOOR
        and eng_ratio < ENGAGEMENT_WEAK
        and discord_msgs < DISCORD_WEAK_MSGS
    )
    has_weak_engagement  = eng_ratio < ENGAGEMENT_WEAK and not has_passive_audience

    # Viral candidate: high resonance + spike OR strong engagement velocity (Feature 12)
    delta = float(row.get("resonance_delta") or 0)
    viral_velocity = delta / max(resonance, 1.0)
    is_viral_candidate = (
        resonance >= 65
        and (has_community_spike or viral_velocity >= VIRAL_VELOCITY)
    )

    # Sentiment from row if pre-computed, else neutral default (Feature 8)
    sentiment_raw   = row.get("sentiment_score")
    if sentiment_raw is not None:
        sent_score  = float(sentiment_raw)
        sent_label  = (
            "positive" if sent_score > 0.4 else
            "negative" if sent_score < -0.4 else
            "neutral"
        )
    else:
        sent_label, sent_score = "neutral", 0.0

    # ── Signal summary sentence ────────────────────────────────────────────
    parts: list[str] = []
    if is_false_popularity:
        parts.append("false popularity (high views, low retention, silent community)")
    elif is_underperformer:
        if has_weak_retention:
            parts.append(f"low retention ({watch_pct:.0f}%)")
        if discord_msgs < DISCORD_WEAK_MSGS:
            parts.append("community silence")
        if has_weak_engagement:
            parts.append(f"weak engagement ratio ({eng_ratio:.3f})")
    if is_high_resonance:
        parts.append(f"strong resonance ({resonance:.0f})")
    if has_community_spike:
        parts.append(f"community spike ({spike_ratio:.1f}× baseline)")
    if is_viral_candidate:
        parts.append("breakout potential")

    summary = f"{title}: " + (", ".join(parts) if parts else "no strong signals detected")

    return VideoSignal(
        video_id            = video_id,
        title               = title,
        topic               = topic,
        is_high_resonance   = is_high_resonance,
        is_underperformer   = is_underperformer,
        is_false_popularity = is_false_popularity,
        is_viral_candidate  = is_viral_candidate,
        has_community_spike = has_community_spike,
        has_weak_retention  = has_weak_retention,
        has_strong_retention= has_strong_retention,
        has_passive_audience= has_passive_audience,
        has_weak_engagement = has_weak_engagement,
        sentiment_label     = sent_label,
        sentiment_score     = sent_score,
        resonance_score     = resonance,
        watch_pct           = watch_pct,
        discord_msg_count   = discord_msgs,
        spike_ratio         = spike_ratio,
        engagement_ratio    = round(eng_ratio, 4),
        signal_summary      = summary,
    )


# ===========================================================================
# Channel-level pattern detection (Features 4–6, 10–14)
# ===========================================================================

def detect_channel_signals(
    resonance_rows: list[dict[str, Any]],
    trend_rows:     list[dict[str, Any]] | None = None,
    discord_stats:  dict[str, Any] | None       = None,
) -> ChannelSignal:
    """
    Aggregate per-video rows into channel-level pattern detection.
    trend_rows: output of coral/queries/trends.sql (optional enrichment)
    discord_stats: dict from discord_service.py (optional)
    """
    sig = ChannelSignal()

    if not resonance_rows:
        return sig

    # ── Topic dominance (Feature 11) ──────────────────────────────────────
    topic_scores: dict[str, list[float]] = {}
    for row in resonance_rows:
        t = str(row.get("topic", ""))
        if t:
            topic_scores.setdefault(t, []).append(float(row.get("resonance_score", 0)))

    topic_avgs = {t: sum(v) / len(v) for t, v in topic_scores.items()}
    if topic_avgs:
        sig.dominant_topic       = max(topic_avgs, key=lambda t: topic_avgs[t])
        sig.dominant_topic_score = round(topic_avgs[sig.dominant_topic], 1)

    # ── Trend shift: emerging / declining topics (Feature 6) ─────────────
    if trend_rows:
        topic_deltas: dict[str, list[float]] = {}
        for row in trend_rows:
            t = str(row.get("topic", ""))
            d = row.get("resonance_delta")
            if t and d is not None:
                topic_deltas.setdefault(t, []).append(float(d))

        avg_deltas = {t: sum(v) / len(v) for t, v in topic_deltas.items()}
        if avg_deltas:
            sig.emerging_topic  = max(avg_deltas, key=lambda t: avg_deltas[t])
            sig.declining_topic = min(avg_deltas, key=lambda t: avg_deltas[t])

            # Growth opportunities: topics with positive delta AND avg score > threshold
            sig.growth_opportunity_topics = [
                t for t, d in avg_deltas.items()
                if d >= TREND_RISING and topic_avgs.get(t, 0) >= 50
            ]

        # Upload consistency (Feature 10)
        gap_periods = sum(1 for r in trend_rows if int(r.get("flag_upload_gap", 0)) == 1)
        sig.upload_gap_weeks = gap_periods
        if gap_periods >= UPLOAD_GAP_SEVERE:
            sig.upload_consistency_flag = "severe"
        elif gap_periods >= UPLOAD_GAP_WARN:
            sig.upload_consistency_flag = "warning"

        # Audience fatigue (Feature 13): high engagement CV across periods
        eng_ratios = [float(r.get("period_engagement_ratio", 0)) for r in trend_rows]
        if len(eng_ratios) > 2:
            mean_e = sum(eng_ratios) / len(eng_ratios)
            cv     = (
                sum((v - mean_e) ** 2 for v in eng_ratios) / len(eng_ratios)
            ) ** 0.5 / max(mean_e, 0.0001)
            sig.audience_fatigue_flag = cv > FATIGUE_CV_THRESHOLD

    # ── Passive audience (Feature 9) ─────────────────────────────────────
    passive_count = sum(
        1 for row in resonance_rows
        if int(row.get("views", 0)) > PASSIVE_VIEWS_FLOOR
        and float(row.get("engagement_ratio", 0)) < ENGAGEMENT_WEAK
        and int(row.get("discord_msg_count", 0)) < DISCORD_WEAK_MSGS
    )
    sig.audience_is_passive = passive_count > len(resonance_rows) * 0.4

    # ── Community spike (Feature 7) ───────────────────────────────────────
    spike_flags = [
        float(row.get("community_spike_ratio", 1.0)) >= DISCORD_SPIKE_RATIO
        for row in resonance_rows
    ]
    sig.channel_spike_flag = any(spike_flags)
    all_msgs = [int(row.get("discord_msg_count", 0)) for row in resonance_rows]
    sig.avg_discord_msgs   = round(sum(all_msgs) / len(all_msgs), 1) if all_msgs else 0.0

    # ── Sentiment (Feature 8) ─────────────────────────────────────────────
    sent_scores = [
        float(row["sentiment_score"])
        for row in resonance_rows
        if row.get("sentiment_score") is not None
    ]
    if sent_scores:
        avg_sent = sum(sent_scores) / len(sent_scores)
        sig.sentiment_score_avg = round(avg_sent, 3)
        sig.overall_sentiment   = (
            "positive" if avg_sent > 0.3 else
            "negative" if avg_sent < -0.3 else
            "neutral"
        )

    # ── Risk signals (Feature 14) ─────────────────────────────────────────
    risks: list[str] = []
    all_res = [float(r.get("resonance_score", 0)) for r in resonance_rows]
    if all_res:
        avg_res = sum(all_res) / len(all_res)
        if avg_res < RESONANCE_LOW:
            risks.append(f"Channel-average resonance is low ({avg_res:.0f})")
    if sig.audience_fatigue_flag:
        risks.append("Audience engagement fatigue detected")
    if sig.audience_is_passive:
        risks.append("Passive audience pattern across majority of content")
    if sig.upload_consistency_flag == "severe":
        risks.append(f"Severe upload gaps ({sig.upload_gap_weeks} periods missed)")
    if sig.overall_sentiment == "negative":
        risks.append("Negative community sentiment trend")
    sig.risk_signals    = risks
    sig.stagnation_risk = len(risks) >= 2

    return sig


# ===========================================================================
# Intent detection (Feature 1)
# ===========================================================================

# Import classify_intent from prompts without circular dependency
def detect_intent(
    question: str,
    channel_signal: ChannelSignal | None = None,
) -> tuple[str, float]:
    """
    Detect user intent from question text, optionally boosted by channel
    signal context (e.g. if stagnation risk is high, weight GROWTH higher).

    Returns (intent_key, confidence 0–1). (Feature 1)
    """
    # Lazy import to avoid circular dependency with prompts.py
    from ai.prompts import classify_intent, (
        INTENT_UNDERPERFORMANCE, INTENT_RECOMMENDATION, INTENT_AUDIENCE_HEALTH,
        INTENT_GROWTH_FORECAST, INTENT_RESONANCE, INTENT_GROWTH, INTENT_GENERAL_CHAT,
        INTENT_KEYWORDS,
    )

    q_lower    = question.lower()
    base_intent = classify_intent(question)

    # Count keyword hits for confidence scoring
    matched_keywords = sum(
        1 for kw in INTENT_KEYWORDS.get(base_intent, [])
        if kw in q_lower
    )
    keyword_confidence = min(matched_keywords / 3.0, 1.0)

    # Context-aware boost: if stagnation detected and question is vague, lean growth
    if channel_signal and channel_signal.stagnation_risk:
        if base_intent == INTENT_GENERAL_CHAT:
            return INTENT_GROWTH, 0.55

    # Boost confidence if channel context matches the detected intent
    if channel_signal:
        if base_intent == INTENT_UNDERPERFORMANCE and channel_signal.risk_signals:
            keyword_confidence = min(keyword_confidence + 0.3, 1.0)
        if base_intent == INTENT_RECOMMENDATION and channel_signal.growth_opportunity_topics:
            keyword_confidence = min(keyword_confidence + 0.25, 1.0)

    return base_intent, round(max(keyword_confidence, 0.4), 2)


# ===========================================================================
# Recommendation trigger builder (Feature 15)
# ===========================================================================

def build_recommendation_triggers(
    video_signals:  list[VideoSignal],
    channel_signal: ChannelSignal,
) -> list[str]:
    """
    Produce an ordered list of recommendation trigger strings.
    insight_engine.py passes these to recommendations.py as context.
    (Feature 15: Recommendation Triggering)
    """
    triggers: list[str] = []

    # Strongest topic → double down
    if channel_signal.dominant_topic and channel_signal.dominant_topic_score >= RESONANCE_HIGH:
        triggers.append(
            f"double_down:{channel_signal.dominant_topic} "
            f"(score={channel_signal.dominant_topic_score:.0f})"
        )

    # Emerging topic → invest
    if channel_signal.emerging_topic:
        triggers.append(f"invest_in_emerging:{channel_signal.emerging_topic}")

    # Declining topic → reduce or refresh
    if channel_signal.declining_topic:
        triggers.append(f"refresh_or_reduce:{channel_signal.declining_topic}")

    # Weak retention → hook improvement
    weak_retention_vids = [s for s in video_signals if s.has_weak_retention]
    if len(weak_retention_vids) >= 2:
        triggers.append("improve_hook:multiple_videos_below_retention_threshold")

    # Passive audience → community content
    if channel_signal.audience_is_passive:
        triggers.append("community_content:passive_audience_detected")

    # Viral candidates → amplify
    viral = [s for s in video_signals if s.is_viral_candidate]
    if viral:
        triggers.append(f"amplify_viral:{viral[0].title[:40]}")

    # Upload gap → consistency push
    if channel_signal.upload_consistency_flag in ("warning", "severe"):
        triggers.append(f"upload_consistency:{channel_signal.upload_gap_weeks}_week_gap")

    # Stagnation → format experiment
    if channel_signal.stagnation_risk:
        triggers.append("format_experiment:stagnation_risk_detected")

    return triggers


# ===========================================================================
# Context block builder for Claude (Feature 16)
# ===========================================================================

def build_detector_context(
    video_signals:  list[VideoSignal],
    channel_signal: ChannelSignal,
    intent:         str,
) -> str:
    """
    Render a compact markdown block of detected patterns for injection into
    the Claude prompt alongside the analytics context.
    (Feature 16: Multi-Signal Pattern Analysis)
    """
    lines: list[str] = ["## Detected Patterns (pre-analysis)\n"]

    # Intent
    lines.append(f"**Detected intent:** {intent.replace('_', ' ').title()}\n")

    # Channel-level patterns
    if channel_signal.dominant_topic:
        lines.append(
            f"- Dominant topic: **{channel_signal.dominant_topic}** "
            f"(avg resonance {channel_signal.dominant_topic_score:.0f})"
        )
    if channel_signal.emerging_topic:
        lines.append(f"- Emerging topic: **{channel_signal.emerging_topic}** — momentum rising")
    if channel_signal.declining_topic:
        lines.append(f"- Declining topic: **{channel_signal.declining_topic}** — momentum falling")
    if channel_signal.audience_is_passive:
        lines.append("- ⚠ Passive audience pattern detected across majority of content")
    if channel_signal.audience_fatigue_flag:
        lines.append("- ⚠ Audience engagement fatigue signal detected")
    if channel_signal.channel_spike_flag:
        lines.append("- 🔥 Community spike event in this period")
    if channel_signal.stagnation_risk:
        lines.append(f"- 🚨 Stagnation risk: {'; '.join(channel_signal.risk_signals[:2])}")
    if channel_signal.upload_consistency_flag != "ok":
        lines.append(
            f"- Upload consistency: **{channel_signal.upload_consistency_flag}** "
            f"({channel_signal.upload_gap_weeks} gap periods)"
        )
    if channel_signal.overall_sentiment != "neutral":
        lines.append(f"- Community sentiment: **{channel_signal.overall_sentiment}**")

    # Per-video highlights (top 3 most interesting signals)
    interesting = sorted(
        video_signals,
        key=lambda s: (
            s.is_viral_candidate * 3
            + s.has_community_spike * 2
            + s.is_underperformer * 2
            + s.is_high_resonance
        ),
        reverse=True,
    )[:3]

    if interesting:
        lines.append("\n**Video-level signals:**")
        for sig in interesting:
            lines.append(f"  - {sig.signal_summary}")

    return "\n".join(lines)


# ===========================================================================
# Public API — single entry point for insight_engine.py
# ===========================================================================

def run_detection(
    question:       str,
    resonance_rows: list[dict[str, Any]],
    trend_rows:     list[dict[str, Any]] | None = None,
    discord_stats:  dict[str, Any] | None       = None,
) -> DetectionReport:
    """
    Run the full detection pipeline and return a DetectionReport.

    Pipeline:
      1. Detect per-video signals
      2. Aggregate channel-level signals
      3. Detect user intent (with channel context boost)
      4. Build recommendation triggers
      5. Build Claude context block
    """
    logger.debug("detectors: running detection on %d resonance rows", len(resonance_rows))

    # ── 1. Per-video signals ───────────────────────────────────────────────
    video_signals = [detect_video_signals(row) for row in resonance_rows]

    # ── 2. Channel signals ────────────────────────────────────────────────
    channel_signal = detect_channel_signals(resonance_rows, trend_rows, discord_stats)

    # ── 3. Intent ─────────────────────────────────────────────────────────
    intent, confidence = detect_intent(question, channel_signal)

    # ── 4. Recommendation triggers ────────────────────────────────────────
    triggers = build_recommendation_triggers(video_signals, channel_signal)

    # ── 5. Context block ──────────────────────────────────────────────────
    context_block = build_detector_context(video_signals, channel_signal, intent)

    # ── Top-level convenience flags ───────────────────────────────────────
    has_underperformers      = any(s.is_underperformer   for s in video_signals)
    has_high_performers      = any(s.is_high_resonance   for s in video_signals)
    has_viral_candidates     = any(s.is_viral_candidate  for s in video_signals)
    has_growth_opportunities = bool(channel_signal.growth_opportunity_topics)
    has_risk_signals         = bool(channel_signal.risk_signals)

    logger.info(
        "detectors: intent=%s confidence=%.2f underperformers=%s viral=%s risks=%d",
        intent, confidence,
        has_underperformers, has_viral_candidates, len(channel_signal.risk_signals),
    )

    return DetectionReport(
        detected_intent          = intent,
        intent_confidence        = confidence,
        video_signals            = video_signals,
        channel_signal           = channel_signal,
        recommendation_triggers  = triggers,
        detector_context_block   = context_block,
        has_underperformers      = has_underperformers,
        has_high_performers      = has_high_performers,
        has_viral_candidates     = has_viral_candidates,
        has_growth_opportunities = has_growth_opportunities,
        has_risk_signals         = has_risk_signals,
    )
