"""
scoring/resonance_score.py
CreatorPulse · Core Scoring Brain

Role: Calculate the Creator Resonance Score — the signature metric that
      measures how strongly content connects with an audience by combining
      YouTube engagement, Discord community activity, Google Sheets retention,
      and sentiment signals into a single 0–100 score with a plain-English
      explanation of why the score is what it is.

Used by:
  ai/insight_engine.py   — per-video scoring before Claude prompt build
  routes/analytics.py    — batch scoring for dashboard leaderboard
  routes/insights.py     — top/underperformer ranked lists
  ai/detectors.py        — spike bonus and penalty inputs
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight constants — mirrors coral/queries/resonance.sql and constants.py
# Adjust here; every downstream consumer reads from this module.
# ---------------------------------------------------------------------------

WEIGHT_WATCH_PCT:   float = 0.40   # audience retention
WEIGHT_DISCORD:     float = 0.30   # community discussion strength
WEIGHT_ENGAGEMENT:  float = 0.20   # likes + comments / views quality
WEIGHT_SENTIMENT:   float = 0.10   # positive/negative community mood

# Normalisation baselines
DISCORD_BASELINE_MSGS:  int   = 50    # messages per video considered "average"
VIEWS_BASELINE:         int   = 10_000
SPIKE_THRESHOLD_RATIO:  float = 3.0   # spike_ratio >= 3× baseline = bonus

# Score tier boundaries (Feature 12)
TIER_POOR:        tuple[int, int] = (0,  30)
TIER_AVERAGE:     tuple[int, int] = (31, 60)
TIER_STRONG:      tuple[int, int] = (61, 80)
TIER_EXCEPTIONAL: tuple[int, int] = (81, 100)


# ===========================================================================
# Data structures
# ===========================================================================

@dataclass
class ResonanceInput:
    """
    All raw signals required to compute one video's resonance score.
    Pass None for any signal that is unavailable — the scorer will
    degrade gracefully and note the missing signal in the explanation.
    (Feature 17: Mock Mode Compatibility — works with partial/mock data.)
    """
    # Identity
    video_id:    str = ""
    title:       str = ""
    topic:       str = ""

    # YouTube signals
    views:       int   = 0
    likes:       int   = 0
    comments:    int   = 0
    watch_pct:   float = 0.0   # 0–100
    ctr:         float = 0.0   # 0–1 float

    # Discord community signals
    discord_msg_count:      int   = 0
    discord_reply_chains:   int   = 0
    community_spike_ratio:  float = 1.0   # peak day / daily baseline

    # Sentiment [-1.0 negative → +1.0 positive] (from detectors.py)
    sentiment_score: float | None = None

    # Trend adjustment: positive = improving momentum, negative = declining
    trend_delta: float | None = None


@dataclass
class ScoreBreakdown:
    """
    Per-component scores before weighting — exposed for debugging and
    Claude context injection. (Features 2, 16)
    """
    watch_component:      float = 0.0   # 0–40 pts
    discord_component:    float = 0.0   # 0–30 pts
    engagement_component: float = 0.0   # 0–20 pts
    sentiment_component:  float = 0.0   # 0–10 pts
    spike_bonus:          float = 0.0   # additional pts for exceptional spikes
    trend_adjustment:     float = 0.0   # ± pts for momentum direction
    penalty:              float = 0.0   # subtracted for false popularity etc.


@dataclass
class ResonanceResult:
    """
    Full scoring output for one video.
    AI-friendly — designed to be serialised directly into Claude's context.
    (Feature 16: AI-Friendly Output)
    """
    video_id:   str
    title:      str
    topic:      str

    # Core score (Feature 12: normalised 0–100)
    score:           float = 0.0
    tier:            str   = "poor"          # poor | average | strong | exceptional
    tier_label:      str   = "Poor"

    # Component breakdown (Features 2–6)
    breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)

    # Contextual fields (Features 13, 14)
    explanation:       str        = ""
    strong_signals:    list[str]  = field(default_factory=list)
    weak_signals:      list[str]  = field(default_factory=list)

    # Comparative benchmarking (Feature 14) — filled by batch scorer
    vs_channel_avg_pct:  float | None = None   # e.g. +18.0 means 18% above avg
    vs_topic_avg_pct:    float | None = None

    # Source flags
    used_discord_data:  bool = False
    used_sheets_data:   bool = False


@dataclass
class BatchScoreResult:
    """
    Output of score_batch() — includes per-video results plus channel-level
    aggregates for the dashboard hero card. (Feature 15)
    """
    results:            list[ResonanceResult]
    channel_avg_score:  float = 0.0
    channel_top_score:  float = 0.0
    channel_low_score:  float = 0.0
    top_topic:          str   = ""
    topic_scores:       dict[str, float] = field(default_factory=dict)


# ===========================================================================
# Core scoring functions
# ===========================================================================

def _score_watch(watch_pct: float) -> float:
    """
    Component A: audience retention score (0 → 40 pts).
    watch_pct is 0–100; 100% retention → full 40 pts. (Feature 4)
    """
    return round(min(watch_pct / 100.0, 1.0) * (WEIGHT_WATCH_PCT * 100), 2)


def _score_discord(msg_count: int, reply_chains: int) -> float:
    """
    Component B: community discussion strength (0 → 30 pts).
    Normalised against DISCORD_BASELINE_MSGS. Reply depth adds a small
    bonus — threads indicate deeper engagement than one-liner messages.
    (Feature 5)
    """
    volume_score = min(msg_count / DISCORD_BASELINE_MSGS, 1.0)
    # Reply depth bonus: up to +15% on top of volume
    depth_bonus = min(reply_chains / max(msg_count, 1), 1.0) * 0.15
    raw = min(volume_score + depth_bonus, 1.0)
    return round(raw * (WEIGHT_DISCORD * 100), 2)


def _score_engagement(views: int, likes: int, comments: int) -> float:
    """
    Component C: engagement quality ratio (0 → 20 pts).
    (likes + comments) / views — rewards deep audience interaction over
    raw view counts. (Feature 3)
    """
    if views <= 0:
        return 0.0
    ratio = (likes + comments) / views
    # Cap at 0.10 (10% interaction rate = full score — very high for video)
    normalised = min(ratio / 0.10, 1.0)
    return round(normalised * (WEIGHT_ENGAGEMENT * 100), 2)


def _score_sentiment(sentiment: float | None) -> float:
    """
    Component D: community sentiment (0 → 10 pts).
    sentiment_score is -1.0 → +1.0 from detectors.py.
    Neutral (None) → half credit (5 pts). (Feature 6)
    """
    if sentiment is None:
        return round((WEIGHT_SENTIMENT * 100) * 0.5, 2)
    normalised = (sentiment + 1.0) / 2.0   # map -1…+1 → 0…1
    return round(normalised * (WEIGHT_SENTIMENT * 100), 2)


def _spike_bonus(spike_ratio: float) -> float:
    """
    Reward exceptional community bursts with up to +5 bonus pts.
    spike_ratio >= SPIKE_THRESHOLD_RATIO (3×) triggers the bonus.
    Scales smoothly up to 5× baseline → +5 pts. (Feature 10)
    """
    if spike_ratio < SPIKE_THRESHOLD_RATIO:
        return 0.0
    excess = spike_ratio - SPIKE_THRESHOLD_RATIO
    bonus = min(excess / (5.0 - SPIKE_THRESHOLD_RATIO), 1.0) * 5.0
    return round(bonus, 2)


def _trend_adjustment(delta: float | None) -> float:
    """
    Nudge the score ±3 pts based on momentum direction.
    Positive delta (rising trend) → up to +3; negative → down to -3.
    (Feature 9)
    """
    if delta is None:
        return 0.0
    adjustment = max(min(delta / 10.0, 1.0), -1.0) * 3.0
    return round(adjustment, 2)


def _penalty(
    views: int,
    watch_pct: float,
    discord_msg_count: int,
    sentiment: float | None,
) -> float:
    """
    Penalty system — deduct points for false popularity and negative signals.
    (Feature 11)

    Rules:
      - High views + low retention + quiet community → -8 pts (false popularity)
      - High views + low retention only              → -4 pts
      - Strongly negative sentiment                  → -3 pts
    """
    penalty = 0.0

    high_views    = views > VIEWS_BASELINE * 5
    low_retention = watch_pct < 30.0
    silent_community = discord_msg_count < 3

    if high_views and low_retention and silent_community:
        penalty += 8.0                   # false popularity
    elif high_views and low_retention:
        penalty += 4.0

    if sentiment is not None and sentiment < -0.5:
        penalty += 3.0                   # significantly negative community mood

    return round(penalty, 2)


def _classify_tier(score: float) -> tuple[str, str]:
    """Return (tier_key, tier_label) for a given score. (Feature 12)"""
    if score <= TIER_POOR[1]:
        return "poor", "Poor"
    if score <= TIER_AVERAGE[1]:
        return "average", "Average"
    if score <= TIER_STRONG[1]:
        return "strong", "Strong"
    return "exceptional", "Exceptional"


def _build_explanation(
    inp: ResonanceInput,
    bd: ScoreBreakdown,
    score: float,
    strong: list[str],
    weak: list[str],
) -> str:
    """
    Generate a plain-English explanation of the score.
    Never says "based on the data" — states facts directly.
    (Feature 13: Score Explanation Generator)
    """
    parts: list[str] = []

    # Lead with the dominant positive signal
    if strong:
        parts.append(f"{strong[0]} drove this score upward.")

    # Call out retention specifically (most intuitive signal for creators)
    if inp.watch_pct >= 60:
        parts.append(f"Audience retention was strong at {inp.watch_pct:.0f}%.")
    elif inp.watch_pct < 30:
        parts.append(
            f"Retention of {inp.watch_pct:.0f}% suggests viewers left early — "
            "a weak hook or pacing issue is likely."
        )

    # Community angle
    if inp.discord_msg_count > DISCORD_BASELINE_MSGS:
        parts.append(
            f"This content sparked {inp.discord_msg_count} Discord messages — "
            "well above the {DISCORD_BASELINE_MSGS}-message baseline."
        )
    elif inp.discord_msg_count < 5:
        parts.append("The community was largely silent after upload.")

    # Spike event
    if bd.spike_bonus > 0:
        parts.append(
            f"A community activity spike ({inp.community_spike_ratio:.1f}× baseline) "
            "added a resonance bonus."
        )

    # Penalty context
    if bd.penalty >= 8:
        parts.append(
            "Despite high view counts, low retention and community silence "
            "indicate false popularity — clicks without genuine connection."
        )
    elif bd.penalty >= 4:
        parts.append(
            "High views paired with low retention reduced the overall score."
        )

    # Sentiment
    if inp.sentiment_score is not None:
        if inp.sentiment_score > 0.5:
            parts.append("Community sentiment was noticeably positive.")
        elif inp.sentiment_score < -0.5:
            parts.append(
                "Negative community sentiment (confusion or criticism) penalised the score."
            )

    # Weak signals summary
    if weak:
        parts.append(f"Weak area: {weak[0].lower()}.")

    # Trend
    if inp.trend_delta is not None:
        if inp.trend_delta > 3:
            parts.append("Momentum is rising — this topic is gaining traction.")
        elif inp.trend_delta < -3:
            parts.append("Momentum is declining for this topic.")

    return " ".join(parts) if parts else f"Resonance score of {score:.0f} reflects mixed signals across platforms."


# ===========================================================================
# Public API
# ===========================================================================

def score_video(inp: ResonanceInput) -> ResonanceResult:
    """
    Calculate the resonance score for a single video.
    Pure function — no I/O, no side effects. (Feature 18: Fast Calculation)

    Steps:
      1. Compute four weighted components
      2. Apply spike bonus and trend adjustment
      3. Subtract penalties
      4. Clamp to 0–100
      5. Generate explanation and signal lists
    """
    # ── Component scores ──────────────────────────────────────────────────
    watch_comp  = _score_watch(inp.watch_pct)
    disc_comp   = _score_discord(inp.discord_msg_count, inp.discord_reply_chains)
    eng_comp    = _score_engagement(inp.views, inp.likes, inp.comments)
    sent_comp   = _score_sentiment(inp.sentiment_score)

    spike_b     = _spike_bonus(inp.community_spike_ratio)
    trend_adj   = _trend_adjustment(inp.trend_delta)
    pen         = _penalty(inp.views, inp.watch_pct, inp.discord_msg_count, inp.sentiment_score)

    breakdown = ScoreBreakdown(
        watch_component      = watch_comp,
        discord_component    = disc_comp,
        engagement_component = eng_comp,
        sentiment_component  = sent_comp,
        spike_bonus          = spike_b,
        trend_adjustment     = trend_adj,
        penalty              = pen,
    )

    raw_score = (
        watch_comp + disc_comp + eng_comp + sent_comp
        + spike_b + trend_adj - pen
    )
    score = round(max(0.0, min(raw_score, 100.0)), 1)

    # ── Strong / weak signal identification ───────────────────────────────
    strong: list[str] = []
    weak:   list[str] = []

    if watch_comp >= WEIGHT_WATCH_PCT * 100 * 0.75:
        strong.append("Strong audience retention")
    else:
        weak.append("Below-average retention")

    if disc_comp >= WEIGHT_DISCORD * 100 * 0.75:
        strong.append("High community discussion volume")
    elif inp.discord_msg_count < 5:
        weak.append("Community silence after upload")

    if eng_comp >= WEIGHT_ENGAGEMENT * 100 * 0.75:
        strong.append("High engagement quality ratio")
    else:
        weak.append("Low engagement-to-view ratio")

    if spike_b > 0:
        strong.append(f"Community activity spike ({inp.community_spike_ratio:.1f}×)")

    if inp.sentiment_score is not None and inp.sentiment_score > 0.5:
        strong.append("Positive community sentiment")
    elif inp.sentiment_score is not None and inp.sentiment_score < -0.3:
        weak.append("Mixed or negative community sentiment")

    if pen >= 8:
        weak.append("False popularity pattern detected")

    tier_key, tier_label = _classify_tier(score)

    explanation = _build_explanation(inp, breakdown, score, strong, weak)

    return ResonanceResult(
        video_id          = inp.video_id,
        title             = inp.title,
        topic             = inp.topic,
        score             = score,
        tier              = tier_key,
        tier_label        = tier_label,
        breakdown         = breakdown,
        explanation       = explanation,
        strong_signals    = strong,
        weak_signals      = weak,
        used_discord_data = inp.discord_msg_count > 0,
        used_sheets_data  = inp.watch_pct > 0,
    )


def score_batch(inputs: list[ResonanceInput]) -> BatchScoreResult:
    """
    Score multiple videos and compute channel-level aggregates.
    Fills in vs_channel_avg_pct and vs_topic_avg_pct on every result.
    (Feature 15: Batch Score Calculation)
    """
    if not inputs:
        return BatchScoreResult(results=[])

    results = [score_video(inp) for inp in inputs]
    scores  = [r.score for r in results]

    channel_avg = round(sum(scores) / len(scores), 1)
    channel_top = round(max(scores), 1)
    channel_low = round(min(scores), 1)

    # Topic-level averages (Feature 7: Topic-Level Resonance Score)
    topic_buckets: dict[str, list[float]] = {}
    for r in results:
        topic_buckets.setdefault(r.topic, []).append(r.score)
    topic_scores = {
        t: round(sum(v) / len(v), 1)
        for t, v in topic_buckets.items()
    }
    top_topic = max(topic_scores, key=lambda t: topic_scores[t], default="")

    # Fill comparative benchmarks on each result (Feature 14)
    for r in results:
        if channel_avg > 0:
            r.vs_channel_avg_pct = round(
                (r.score - channel_avg) / channel_avg * 100, 1
            )
        topic_avg = topic_scores.get(r.topic, channel_avg)
        if topic_avg > 0:
            r.vs_topic_avg_pct = round(
                (r.score - topic_avg) / topic_avg * 100, 1
            )

    results.sort(key=lambda r: r.score, reverse=True)

    return BatchScoreResult(
        results           = results,
        channel_avg_score = channel_avg,
        channel_top_score = channel_top,
        channel_low_score = channel_low,
        top_topic         = top_topic,
        topic_scores      = topic_scores,
    )


def score_from_row(row: dict[str, Any]) -> ResonanceResult:
    """
    Convenience wrapper: build a ResonanceInput from a Coral query result
    row dict (as returned by coral_client.run_query) and score it.

    Column names match the output of coral/queries/resonance.sql.
    (Feature 17: Mock Mode Compatibility — works with both live and mock rows)
    """
    inp = ResonanceInput(
        video_id              = str(row.get("video_id", "")),
        title                 = str(row.get("title", "")),
        topic                 = str(row.get("topic", "")),
        views                 = int(row.get("views", 0)),
        likes                 = int(row.get("likes", 0)),
        comments              = int(row.get("comments", 0)),
        watch_pct             = float(row.get("watch_pct", 0.0)),
        ctr                   = float(row.get("ctr", 0.0)),
        discord_msg_count     = int(row.get("discord_msg_count", 0)),
        discord_reply_chains  = int(row.get("discord_reply_chains", 0)),
        community_spike_ratio = float(row.get("community_spike_ratio", 1.0)),
        sentiment_score       = row.get("sentiment_score"),      # may be None
        trend_delta           = row.get("resonance_delta"),      # from trends.sql
    )
    return score_video(inp)


def score_batch_from_rows(rows: list[dict[str, Any]]) -> BatchScoreResult:
    """
    Batch version of score_from_row — accepts a list of Coral result dicts.
    Used by routes/analytics.py to score all videos in one call.
    (Feature 15)
    """
    inputs = [
        ResonanceInput(
            video_id              = str(r.get("video_id", "")),
            title                 = str(r.get("title", "")),
            topic                 = str(r.get("topic", "")),
            views                 = int(r.get("views", 0)),
            likes                 = int(r.get("likes", 0)),
            comments              = int(r.get("comments", 0)),
            watch_pct             = float(r.get("watch_pct", 0.0)),
            ctr                   = float(r.get("ctr", 0.0)),
            discord_msg_count     = int(r.get("discord_msg_count", 0)),
            discord_reply_chains  = int(r.get("discord_reply_chains", 0)),
            community_spike_ratio = float(r.get("community_spike_ratio", 1.0)),
            sentiment_score       = r.get("sentiment_score"),
            trend_delta           = r.get("resonance_delta"),
        )
        for r in rows
    ]
    return score_batch(inputs)
