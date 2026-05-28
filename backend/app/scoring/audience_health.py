"""
scoring/audience_health.py
CreatorPulse · Audience Quality Intelligence Engine

Role: Answers "Is your audience healthy?" — a channel-level metric that
      aggregates retention, community loyalty, engagement consistency,
      sentiment, and growth sustainability into a single 0–100 Audience
      Health Score with a plain-English narrative and actionable recommendations.

Complements resonance_score.py (per-video) by operating at the channel level:
  resonance_score.py  → "Did this content resonate?"
  audience_health.py  → "Is your audience healthy overall?"

Used by:
  routes/analytics.py      — /analytics/summary dashboard hero card
  ai/insight_engine.py     — channel health context for Claude
  ai/recommendations.py    — audience improvement suggestions
  scoring/growth_predictor.py — sustainability input signal
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight constants
# Tuned so each pillar contributes meaningfully; adjust in one place.
# ---------------------------------------------------------------------------

WEIGHT_RETENTION:    float = 0.30   # watch % consistency
WEIGHT_COMMUNITY:    float = 0.25   # Discord activity depth
WEIGHT_LOYALTY:      float = 0.20   # repeat engagement
WEIGHT_SENTIMENT:    float = 0.15   # community mood
WEIGHT_CONSISTENCY:  float = 0.10   # upload + engagement regularity

# Health category thresholds (Feature 14)
HEALTH_EXCELLENT:  int = 85
HEALTH_HEALTHY:    int = 70
HEALTH_AVERAGE:    int = 50
HEALTH_AT_RISK:    int = 35
# < AT_RISK → "Weak"

# Passive audience threshold: engagement ratio below this = silent viewers
PASSIVE_ENGAGEMENT_RATIO: float = 0.01

# Burnout signal: engagement drop of this % period-over-period
BURNOUT_DROP_THRESHOLD: float = -0.20

# Loyalty signal: fraction of Discord users returning week-over-week
LOYALTY_STRONG_THRESHOLD: float = 0.55


# ===========================================================================
# Input / output data structures
# ===========================================================================

@dataclass
class AudienceHealthInput:
    """
    All signals needed to compute the channel-level Audience Health Score.
    Every field has a safe default so mock data works identically to live data.
    (Feature 18: Mock Mode Compatibility)

    Aggregated from:
      coral/queries/resonance.sql  — per-video averages
      coral/queries/trends.sql     — period-over-period deltas
      discord_service.py           — community loyalty signals
    """
    # ── Retention health (Feature 4) ──────────────────────────────────────
    avg_watch_pct:           float = 0.0    # channel-wide average 0–100
    watch_pct_trend_delta:   float = 0.0    # change vs previous period

    # ── Community activity (Feature 3) ───────────────────────────────────
    avg_discord_msgs_per_video:   int   = 0
    discord_active_members:       int   = 0
    discord_reply_depth_ratio:    float = 0.0   # replies / messages

    # ── Loyal audience (Feature 2, 10) ───────────────────────────────────
    returning_discord_ratio:  float = 0.0   # 0–1; fraction of users seen last period
    repeat_commenter_ratio:   float = 0.0   # 0–1; YouTube comments from repeat users

    # ── Engagement consistency (Feature 5) ───────────────────────────────
    engagement_ratio_avg:        float = 0.0   # (likes + comments) / views
    engagement_consistency_cv:   float = 0.0   # coefficient of variation (lower = more consistent)
    videos_per_week_avg:         float = 0.0   # upload frequency
    upload_gap_weeks:            int   = 0     # longest gap in uploads (weeks)

    # ── Sentiment (Feature 7, 11) ─────────────────────────────────────────
    avg_sentiment:       float | None = None   # -1.0 → +1.0
    negative_spike_flag: bool         = False  # True if recent toxicity detected

    # ── Trend context (Feature 13) ────────────────────────────────────────
    resonance_trend_delta:   float = 0.0   # avg resonance score change this period
    community_trend_delta:   float = 0.0   # Discord msg count change this period

    # ── Period metadata ────────────────────────────────────────────────────
    period_days:   int = 30
    total_videos:  int = 0


@dataclass
class HealthPillarScores:
    """
    Per-pillar breakdown before weighting — exposed for debugging and
    Claude context injection. (Feature 17: AI-Friendly Output)
    """
    retention_score:    float = 0.0   # 0–30 pts
    community_score:    float = 0.0   # 0–25 pts
    loyalty_score:      float = 0.0   # 0–20 pts
    sentiment_score:    float = 0.0   # 0–15 pts
    consistency_score:  float = 0.0   # 0–10 pts

    # Adjustments
    burnout_penalty:    float = 0.0   # subtracted for fatigue signals
    passive_penalty:    float = 0.0   # subtracted for silent-audience pattern
    growth_bonus:       float = 0.0   # added for improving trends


@dataclass
class AudienceHealthResult:
    """
    Full audience health output — AI-friendly and dashboard-ready.
    (Features 14, 16, 17)
    """
    # ── Core score ─────────────────────────────────────────────────────────
    health_score:    float = 0.0
    health_category: str   = "weak"           # excellent | healthy | average | at_risk | weak
    health_label:    str   = "Weak Audience"

    # ── Pillar breakdown ────────────────────────────────────────────────────
    pillars: HealthPillarScores = field(default_factory=HealthPillarScores)

    # ── Derived indices (dashboard widgets) ────────────────────────────────
    loyalty_index:         float = 0.0    # 0–100 composite loyalty score (Feature 10)
    community_activity:    float = 0.0    # 0–100 Discord activity health (Feature 3)
    retention_health:      float = 0.0    # 0–100 retention quality (Feature 4)
    growth_sustainability: float = 0.0    # 0–100 sustainable vs spike-driven (Feature 9)

    # ── Flags ───────────────────────────────────────────────────────────────
    flag_passive_audience:  bool = False   # Feature 6
    flag_burnout:           bool = False   # Feature 8
    flag_negative_sentiment: bool = False  # Feature 11
    flag_upload_inconsistency: bool = False

    # ── Trend ───────────────────────────────────────────────────────────────
    trend_direction: str   = "stable"     # rising | stable | declining (Feature 13)
    trend_summary:   str   = ""

    # ── AI context ──────────────────────────────────────────────────────────
    strong_signals:    list[str] = field(default_factory=list)
    weak_signals:      list[str] = field(default_factory=list)
    explanation:       str       = ""
    recommendations:   list[str] = field(default_factory=list)   # Feature 15

    # ── Topic fit ───────────────────────────────────────────────────────────
    best_fit_topic:  str   = ""    # Feature 12
    worst_fit_topic: str   = ""


# ===========================================================================
# Pillar scoring helpers (all return pts within their weight ceiling)
# ===========================================================================

def _score_retention(avg_watch_pct: float, trend_delta: float) -> float:
    """
    Retention pillar (0 → 30 pts).
    watch_pct 100% → full 30; below 30% → near 0.
    Improving trend adds a fractional bonus already captured in growth_bonus.
    (Feature 4)
    """
    normalised = min(avg_watch_pct / 100.0, 1.0)
    return round(normalised * (WEIGHT_RETENTION * 100), 2)


def _score_community(
    msgs_per_video: int,
    active_members: int,
    reply_ratio: float,
) -> float:
    """
    Community activity pillar (0 → 25 pts).
    Combines message volume (normalised to 50-msg baseline), active member
    count (normalised to 200 members), and reply depth. (Feature 3)
    """
    vol_norm    = min(msgs_per_video / 50, 1.0)
    member_norm = min(active_members / 200, 1.0)
    depth_norm  = min(reply_ratio / 0.3, 1.0)    # 30% reply ratio = full

    combined = (vol_norm * 0.5 + member_norm * 0.3 + depth_norm * 0.2)
    return round(combined * (WEIGHT_COMMUNITY * 100), 2)


def _score_loyalty(
    returning_discord_ratio: float,
    repeat_commenter_ratio: float,
) -> float:
    """
    Loyalty pillar (0 → 20 pts).
    Rewards returning Discord users and repeat YouTube commenters — the
    clearest signal of a tight creator-community bond. (Features 2, 10)
    """
    discord_loyalty  = min(returning_discord_ratio, 1.0)
    youtube_loyalty  = min(repeat_commenter_ratio, 1.0)
    combined = discord_loyalty * 0.6 + youtube_loyalty * 0.4
    return round(combined * (WEIGHT_LOYALTY * 100), 2)


def _score_sentiment(avg_sentiment: float | None, negative_spike: bool) -> float:
    """
    Sentiment pillar (0 → 15 pts).
    Maps -1…+1 → 0…15.  Negative spike flag halves the score. (Features 7, 11)
    """
    if avg_sentiment is None:
        base = (WEIGHT_SENTIMENT * 100) * 0.5    # neutral = half credit
    else:
        normalised = (avg_sentiment + 1.0) / 2.0
        base = normalised * (WEIGHT_SENTIMENT * 100)

    if negative_spike:
        base *= 0.5    # toxicity / confusion event detected
    return round(base, 2)


def _score_consistency(
    engagement_cv: float,
    videos_per_week: float,
    upload_gap_weeks: int,
) -> float:
    """
    Upload + engagement consistency pillar (0 → 10 pts).
    Low coefficient of variation = stable engagement.
    Upload gaps penalised proportionally. (Feature 5, Feature 8)
    """
    # CV: 0 = perfectly consistent, >1 = very erratic
    eng_stability = max(0.0, 1.0 - min(engagement_cv, 1.0))

    # Upload frequency: 1+ video/week = full; less = proportional
    upload_norm = min(videos_per_week, 1.0)

    # Gap penalty: each week of gap above 2 reduces score
    gap_penalty = max(0.0, min((upload_gap_weeks - 2) / 10.0, 0.5))

    combined = (eng_stability * 0.6 + upload_norm * 0.4) - gap_penalty
    combined = max(0.0, combined)
    return round(combined * (WEIGHT_CONSISTENCY * 100), 2)


def _burnout_penalty(
    watch_trend: float,
    community_trend: float,
    engagement_cv: float,
) -> float:
    """
    Deduct up to 8 pts when multiple signals suggest audience fatigue.
    (Feature 8: Audience Burnout Detection)
    """
    penalty = 0.0
    if watch_trend < BURNOUT_DROP_THRESHOLD:
        penalty += 4.0
    if community_trend < BURNOUT_DROP_THRESHOLD:
        penalty += 4.0
    return round(min(penalty, 8.0), 2)


def _passive_penalty(engagement_ratio: float, msgs_per_video: int) -> float:
    """
    Deduct up to 6 pts for passive audience pattern:
    many views but almost no interaction or community discussion.
    (Feature 6: Passive Audience Detection)
    """
    if engagement_ratio < PASSIVE_ENGAGEMENT_RATIO and msgs_per_video < 5:
        return 6.0
    if engagement_ratio < PASSIVE_ENGAGEMENT_RATIO:
        return 3.0
    return 0.0


def _growth_bonus(resonance_delta: float, community_delta: float) -> float:
    """
    Add up to +4 pts when both resonance and community trends are positive —
    signals healthy, sustainable growth rather than a one-off spike.
    (Feature 9: Growth Sustainability Score)
    """
    if resonance_delta > 3.0 and community_delta > 0:
        return 4.0
    if resonance_delta > 1.0 or community_delta > 0:
        return 2.0
    return 0.0


def _classify_health(score: float) -> tuple[str, str]:
    """Return (category_key, display_label). (Feature 14)"""
    if score >= HEALTH_EXCELLENT:
        return "excellent", "Excellent Audience"
    if score >= HEALTH_HEALTHY:
        return "healthy", "Healthy Audience"
    if score >= HEALTH_AVERAGE:
        return "average", "Average Audience"
    if score >= HEALTH_AT_RISK:
        return "at_risk", "At Risk"
    return "weak", "Weak Audience"


def _build_explanation(
    inp: AudienceHealthInput,
    pillars: HealthPillarScores,
    score: float,
    strong: list[str],
    weak: list[str],
    flags: dict[str, bool],
) -> str:
    """
    Generate a plain-English narrative about audience health.
    Mirrors the no-filler style of resonance_score.py. (Feature 17)
    """
    parts: list[str] = []

    if strong:
        parts.append(f"{strong[0]} is your audience's greatest asset.")

    if inp.avg_watch_pct >= 60:
        parts.append(f"Retention is strong at {inp.avg_watch_pct:.0f}% average.")
    elif inp.avg_watch_pct < 30:
        parts.append(f"Retention of {inp.avg_watch_pct:.0f}% signals weak content-audience fit.")

    if inp.returning_discord_ratio >= LOYALTY_STRONG_THRESHOLD:
        parts.append(
            f"{inp.returning_discord_ratio * 100:.0f}% of Discord members returned this period — "
            "strong community loyalty."
        )
    elif inp.returning_discord_ratio > 0 and inp.returning_discord_ratio < 0.3:
        parts.append("Low Discord return rate suggests the community lacks a sticky reason to come back.")

    if flags.get("passive"):
        parts.append(
            "Audience watches but rarely interacts — a passive viewer pattern. "
            "Community-first content could convert viewers into participants."
        )

    if flags.get("burnout"):
        parts.append(
            "Multiple signals point to audience fatigue: "
            "both retention and community activity declined this period."
        )

    if flags.get("negative_sentiment"):
        parts.append("Community sentiment has turned negative — worth reviewing recent feedback.")

    if inp.resonance_trend_delta > 3:
        parts.append(
            f"Resonance improved by {inp.resonance_trend_delta:+.1f} points this period — "
            "audience health is trending upward."
        )
    elif inp.resonance_trend_delta < -3:
        parts.append(f"Resonance dropped by {abs(inp.resonance_trend_delta):.1f} points this period.")

    if weak:
        parts.append(f"Priority improvement area: {weak[0].lower()}.")

    return " ".join(parts) if parts else f"Overall audience health score is {score:.0f}/100."


def _build_recommendations(
    inp: AudienceHealthInput,
    flags: dict[str, bool],
    weak: list[str],
    best_topic: str,
    worst_topic: str,
) -> list[str]:
    """
    Generate 1–4 ranked, actionable recommendations.
    (Feature 15: Recommendation Support)
    """
    recs: list[str] = []

    if flags.get("passive"):
        recs.append(
            "Run a community-first challenge or Q&A in Discord to convert passive viewers "
            "into active participants."
        )

    if inp.avg_watch_pct < 40 and inp.avg_watch_pct > 0:
        recs.append(
            f"Retention averages {inp.avg_watch_pct:.0f}% — test a stronger hook in the first "
            "30 seconds and tighter pacing to push above 50%."
        )

    if flags.get("burnout"):
        recs.append(
            "Engagement fatigue detected. Consider a content format change or a short "
            "break before the next upload cycle."
        )

    if best_topic and worst_topic and best_topic != worst_topic:
        recs.append(
            f"'{best_topic}' content drives your healthiest audience engagement. "
            f"'{worst_topic}' consistently underperforms — consider reducing its frequency."
        )

    if inp.upload_gap_weeks > 2:
        recs.append(
            f"A {inp.upload_gap_weeks}-week upload gap correlated with a drop in "
            "community activity. Aim for at least one video per week to maintain momentum."
        )

    if flags.get("negative_sentiment"):
        recs.append(
            "Address the negative sentiment signals directly — a community update video "
            "or Discord AMA can reset audience mood."
        )

    return recs[:4]   # cap at 4 so the dashboard card stays clean


# ===========================================================================
# Derived index helpers (for dashboard widgets)
# ===========================================================================

def _loyalty_index(returning_discord: float, repeat_commenter: float) -> float:
    """Composite loyalty index 0–100. (Feature 10)"""
    raw = returning_discord * 0.6 + repeat_commenter * 0.4
    return round(min(raw, 1.0) * 100, 1)


def _community_activity_index(msgs: int, members: int, reply_ratio: float) -> float:
    """Community activity 0–100. (Feature 3)"""
    raw = (
        min(msgs / 50, 1.0) * 0.5
        + min(members / 200, 1.0) * 0.3
        + min(reply_ratio / 0.3, 1.0) * 0.2
    )
    return round(raw * 100, 1)


def _growth_sustainability_index(
    resonance_delta: float,
    community_delta: float,
    engagement_cv: float,
) -> float:
    """
    Measures steady, sustainable growth vs one-off viral spikes.
    Low CV + positive deltas = high sustainability. (Feature 9)
    """
    stability = max(0.0, 1.0 - min(engagement_cv, 1.0))
    momentum  = max(0.0, min((resonance_delta + community_delta / 10.0) / 10.0, 1.0))
    return round((stability * 0.6 + momentum * 0.4) * 100, 1)


# ===========================================================================
# Public API
# ===========================================================================

def score_audience_health(inp: AudienceHealthInput) -> AudienceHealthResult:
    """
    Calculate the channel-level Audience Health Score (0–100).
    Pure function — no I/O, no side effects.

    Pipeline:
      1. Score five pillars independently
      2. Apply burnout, passive, and growth adjustments
      3. Clamp final score to 0–100
      4. Classify tier, build explanation and recommendations
    """
    # ── Pillar scores ──────────────────────────────────────────────────────
    ret_score  = _score_retention(inp.avg_watch_pct, inp.watch_pct_trend_delta)
    comm_score = _score_community(
        inp.avg_discord_msgs_per_video,
        inp.discord_active_members,
        inp.discord_reply_depth_ratio,
    )
    loy_score  = _score_loyalty(inp.returning_discord_ratio, inp.repeat_commenter_ratio)
    sent_score = _score_sentiment(inp.avg_sentiment, inp.negative_spike_flag)
    cons_score = _score_consistency(
        inp.engagement_consistency_cv,
        inp.videos_per_week_avg,
        inp.upload_gap_weeks,
    )

    # ── Adjustments ────────────────────────────────────────────────────────
    burn_pen    = _burnout_penalty(
        inp.watch_pct_trend_delta,
        inp.community_trend_delta,
        inp.engagement_consistency_cv,
    )
    passive_pen = _passive_penalty(inp.engagement_ratio_avg, inp.avg_discord_msgs_per_video)
    growth_bon  = _growth_bonus(inp.resonance_trend_delta, inp.community_trend_delta)

    pillars = HealthPillarScores(
        retention_score   = ret_score,
        community_score   = comm_score,
        loyalty_score     = loy_score,
        sentiment_score   = sent_score,
        consistency_score = cons_score,
        burnout_penalty   = burn_pen,
        passive_penalty   = passive_pen,
        growth_bonus      = growth_bon,
    )

    raw = (
        ret_score + comm_score + loy_score + sent_score + cons_score
        + growth_bon - burn_pen - passive_pen
    )
    health_score = round(max(0.0, min(raw, 100.0)), 1)

    # ── Flags ─────────────────────────────────────────────────────────────
    flag_passive   = passive_pen > 0
    flag_burnout   = burn_pen > 0
    flag_negative  = inp.negative_spike_flag or (
        inp.avg_sentiment is not None and inp.avg_sentiment < -0.4
    )
    flag_upload_gap = inp.upload_gap_weeks > 2

    flags = {
        "passive":          flag_passive,
        "burnout":          flag_burnout,
        "negative_sentiment": flag_negative,
        "upload_gap":       flag_upload_gap,
    }

    # ── Trend direction ───────────────────────────────────────────────────
    combined_trend = inp.resonance_trend_delta + inp.community_trend_delta / 5.0
    if combined_trend > 2:
        trend_direction = "rising"
        trend_summary   = f"Audience health trending upward ({combined_trend:+.1f} pts)."
    elif combined_trend < -2:
        trend_direction = "declining"
        trend_summary   = f"Audience health declining ({combined_trend:+.1f} pts)."
    else:
        trend_direction = "stable"
        trend_summary   = "Audience health is stable this period."

    # ── Strong / weak signal identification ──────────────────────────────
    strong: list[str] = []
    weak:   list[str] = []

    if ret_score >= WEIGHT_RETENTION * 100 * 0.75:
        strong.append("Strong audience retention")
    else:
        weak.append("Below-average retention")

    if loy_score >= WEIGHT_LOYALTY * 100 * 0.75:
        strong.append("High community loyalty")
    elif inp.returning_discord_ratio < 0.25:
        weak.append("Low returning-member rate")

    if comm_score >= WEIGHT_COMMUNITY * 100 * 0.75:
        strong.append("Active community discussion")
    elif flag_passive:
        weak.append("Passive audience — low interaction")

    if flag_burnout:
        weak.append("Audience engagement fatigue")

    if growth_bon > 0:
        strong.append("Positive growth momentum")

    # ── Derived dashboard indices ─────────────────────────────────────────
    loyalty_idx     = _loyalty_index(inp.returning_discord_ratio, inp.repeat_commenter_ratio)
    community_idx   = _community_activity_index(
        inp.avg_discord_msgs_per_video,
        inp.discord_active_members,
        inp.discord_reply_depth_ratio,
    )
    sustainability  = _growth_sustainability_index(
        inp.resonance_trend_delta,
        inp.community_trend_delta,
        inp.engagement_consistency_cv,
    )
    retention_idx   = round(min(inp.avg_watch_pct, 100.0), 1)

    # ── Classification ────────────────────────────────────────────────────
    category, label = _classify_health(health_score)

    explanation     = _build_explanation(inp, pillars, health_score, strong, weak, flags)
    recommendations = _build_recommendations(inp, flags, weak, "", "")

    return AudienceHealthResult(
        health_score             = health_score,
        health_category          = category,
        health_label             = label,
        pillars                  = pillars,
        loyalty_index            = loyalty_idx,
        community_activity       = community_idx,
        retention_health         = retention_idx,
        growth_sustainability    = sustainability,
        flag_passive_audience    = flag_passive,
        flag_burnout             = flag_burnout,
        flag_negative_sentiment  = flag_negative,
        flag_upload_inconsistency = flag_upload_gap,
        trend_direction          = trend_direction,
        trend_summary            = trend_summary,
        strong_signals           = strong,
        weak_signals             = weak,
        explanation              = explanation,
        recommendations          = recommendations,
    )


def score_audience_health_from_rows(
    resonance_rows: list[dict[str, Any]],
    trend_rows:     list[dict[str, Any]],
    discord_stats:  dict[str, Any] | None = None,
) -> AudienceHealthResult:
    """
    Convenience builder: aggregate Coral query result rows into an
    AudienceHealthInput and score it.

    resonance_rows: output of coral/queries/resonance.sql
    trend_rows:     output of coral/queries/trends.sql
    discord_stats:  optional dict from discord_service.py with loyalty data
    (Feature 18: Mock Mode — works with empty / mock row lists)
    """
    if not resonance_rows:
        logger.warning("audience_health: no resonance rows — returning mock score")
        return score_audience_health(AudienceHealthInput())

    # ── Aggregate from resonance rows ──────────────────────────────────────
    total         = len(resonance_rows)
    avg_watch     = sum(float(r.get("watch_pct", 0)) for r in resonance_rows) / total
    avg_eng       = sum(float(r.get("engagement_ratio", 0)) for r in resonance_rows) / total
    avg_msgs      = int(sum(float(r.get("discord_msg_count", 0)) for r in resonance_rows) / total)
    avg_sentiment_vals = [
        r["sentiment_score"] for r in resonance_rows if r.get("sentiment_score") is not None
    ]
    avg_sentiment = (
        sum(avg_sentiment_vals) / len(avg_sentiment_vals)
        if avg_sentiment_vals else None
    )

    # ── Aggregate from trend rows ──────────────────────────────────────────
    res_deltas    = [float(r.get("resonance_delta", 0)) for r in trend_rows if r.get("resonance_delta") is not None]
    comm_deltas   = [float(r.get("discord_messages_delta", 0)) for r in trend_rows if r.get("discord_messages_delta") is not None]
    watch_deltas  = [float(r.get("watch_pct_delta", 0)) for r in trend_rows if r.get("watch_pct_delta") is not None]

    res_delta_avg  = sum(res_deltas)  / len(res_deltas)  if res_deltas  else 0.0
    comm_delta_avg = sum(comm_deltas) / len(comm_deltas) if comm_deltas else 0.0
    watch_delta_avg = sum(watch_deltas) / len(watch_deltas) if watch_deltas else 0.0

    # Upload gap: longest streak of zero-video periods
    upload_gaps = [
        int(r.get("upload_frequency_delta", 0)) for r in trend_rows
        if int(r.get("flag_upload_gap", 0)) == 1
    ]
    upload_gap_weeks = len(upload_gaps)

    # Videos per week average
    vids_per_period = [float(r.get("videos_published", 0)) for r in trend_rows]
    videos_per_week = sum(vids_per_period) / len(vids_per_period) if vids_per_period else 0.0

    # Engagement CV (std / mean)
    eng_ratios = [float(r.get("period_engagement_ratio", 0)) for r in trend_rows]
    if len(eng_ratios) > 1:
        mean_eng = sum(eng_ratios) / len(eng_ratios)
        variance = sum((x - mean_eng) ** 2 for x in eng_ratios) / len(eng_ratios)
        eng_cv   = (variance ** 0.5) / mean_eng if mean_eng > 0 else 0.0
    else:
        eng_cv = 0.0

    # ── Discord loyalty stats (from discord_service.py or defaults) ────────
    ds = discord_stats or {}
    returning_discord = float(ds.get("returning_ratio", 0.0))
    repeat_commenter  = float(ds.get("repeat_commenter_ratio", 0.0))
    active_members    = int(ds.get("active_members", 0))
    reply_ratio       = float(ds.get("reply_depth_ratio", 0.0))
    negative_spike    = bool(ds.get("negative_spike_flag", False))

    inp = AudienceHealthInput(
        avg_watch_pct                = avg_watch,
        watch_pct_trend_delta        = watch_delta_avg,
        avg_discord_msgs_per_video   = avg_msgs,
        discord_active_members       = active_members,
        discord_reply_depth_ratio    = reply_ratio,
        returning_discord_ratio      = returning_discord,
        repeat_commenter_ratio       = repeat_commenter,
        engagement_ratio_avg         = avg_eng,
        engagement_consistency_cv    = round(eng_cv, 4),
        videos_per_week_avg          = videos_per_week,
        upload_gap_weeks             = upload_gap_weeks,
        avg_sentiment                = avg_sentiment,
        negative_spike_flag          = negative_spike,
        resonance_trend_delta        = res_delta_avg,
        community_trend_delta        = comm_delta_avg,
        period_days                  = 30,
        total_videos                 = total,
    )

    return score_audience_health(inp)
