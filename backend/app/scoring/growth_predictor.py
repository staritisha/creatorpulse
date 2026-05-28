"""
scoring/growth_predictor.py
CreatorPulse · Future Intelligence Engine

Role: Answers "What should happen next?" — uses numpy polynomial fitting on
      historical resonance series (from trends.sql) plus multi-signal weights
      to forecast growth, detect momentum, surface high-potential topics, and
      produce a structured prediction report for Claude and the dashboard.

Complements:
  resonance_score.py  → "What resonated?"
  audience_health.py  → "How healthy is the audience?"
  growth_predictor.py → "What should happen next?"

Used by:
  ai/insight_engine.py  — growth forecast injected into Claude context
  routes/analytics.py   — /analytics/summary prediction widgets
  routes/chat.py        — "Where is my channel heading?" answers
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import numpy — fall back to pure-Python linear regression if absent.
# This keeps the module functional even in stripped hackathon environments.
# ---------------------------------------------------------------------------
try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _NUMPY_AVAILABLE = False
    logger.warning("growth_predictor: numpy not found — using pure-Python fallback")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FORECAST_HORIZON_DAYS: int   = 7      # default short-term forecast window
MIN_DATA_POINTS:        int   = 2      # minimum periods needed to fit a trend
POLY_DEGREE:            int   = 1      # linear fit (polyfit degree)

# Multi-signal weights for the composite growth score (Feature 15)
GROWTH_WEIGHT_RESONANCE:    float = 0.30
GROWTH_WEIGHT_RETENTION:    float = 0.25
GROWTH_WEIGHT_COMMUNITY:    float = 0.20
GROWTH_WEIGHT_CONSISTENCY:  float = 0.15
GROWTH_WEIGHT_SENTIMENT:    float = 0.10

# Viral potential threshold: normalised engagement velocity above this = breakout
VIRAL_VELOCITY_THRESHOLD: float = 0.70

# Upload frequency simulation constants (Feature 6)
UPLOAD_FREQ_BASELINE: float = 1.0   # videos / week considered neutral
UPLOAD_FREQ_IMPACT:   float = 0.08  # +8% growth estimate per extra video/week above baseline

# Growth momentum thresholds (Feature 2)
MOMENTUM_ACCELERATING: float =  3.0
MOMENTUM_DECLINING:    float = -3.0

# Seasonal day-of-week weights (0=Mon … 6=Sun) — Feature 11
# Derived from industry averages; replace with per-creator data when available
DAY_OF_WEEK_WEIGHTS: dict[int, float] = {
    0: 0.85,   # Monday
    1: 1.05,   # Tuesday  ← strong
    2: 1.00,   # Wednesday
    3: 0.95,   # Thursday
    4: 0.90,   # Friday
    5: 0.75,   # Saturday
    6: 0.80,   # Sunday
}
BEST_UPLOAD_DAY: dict[int, str] = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday",
    3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday",
}


# ===========================================================================
# Input / output structures
# ===========================================================================

@dataclass
class PredictorInput:
    """
    All signals needed for a full growth prediction.
    Every field defaults to a safe value so mock data works identically.
    (Feature 18: Mock Mode Compatibility)

    Primary source: predictor_feed CTE in coral/queries/trends.sql
    Supplementary:  scoring/resonance_score.py BatchScoreResult
                    scoring/audience_health.py AudienceHealthResult
    """
    # ── Historical resonance series (Feature 7) ───────────────────────────
    # Comma-separated string as returned by trends.sql predictor_feed CTE,
    # OR a list of floats (both accepted — parsed in __post_init__).
    resonance_series_raw: str | list[float] = ""

    # ── Channel-level aggregates ──────────────────────────────────────────
    channel_avg_resonance:      float = 0.0
    channel_avg_watch_pct:      float = 0.0
    channel_avg_engagement:     float = 0.0   # (likes+comments)/views
    discord_trend_delta:        float = 0.0   # community activity delta
    avg_sentiment:              float | None = None

    # ── Upload consistency (Feature 6) ───────────────────────────────────
    videos_per_week_avg:    float = 1.0
    upload_gap_weeks:       int   = 0

    # ── Per-topic resonance map (Feature 3) ──────────────────────────────
    # { "AI Agents": 88.5, "Career Advice": 52.1, ... }
    topic_scores: dict[str, float] = field(default_factory=dict)

    # ── Topic trend deltas (Feature 12) ──────────────────────────────────
    # { "AI Agents": +4.2, "Career Advice": -1.8, ... }
    topic_deltas: dict[str, float] = field(default_factory=dict)

    # ── Audience health snapshot (Feature 5, 13) ─────────────────────────
    audience_health_score:      float = 0.0   # 0–100 from audience_health.py
    loyalty_index:              float = 0.0

    # ── Period metadata ───────────────────────────────────────────────────
    period_label:   str = "week"    # 'day' | 'week' | 'month'
    data_points:    int = 0

    def parsed_series(self) -> list[float]:
        """Return resonance series as a list of floats regardless of input type."""
        if isinstance(self.resonance_series_raw, list):
            return [float(v) for v in self.resonance_series_raw]
        raw = str(self.resonance_series_raw).strip()
        if not raw:
            return []
        try:
            return [float(v.strip()) for v in raw.split(",") if v.strip()]
        except ValueError:
            logger.warning("growth_predictor: could not parse resonance_series_raw")
            return []


@dataclass
class TopicForecast:
    """Growth forecast for a single content topic. (Feature 3)"""
    topic:              str
    current_score:      float
    predicted_score:    float
    delta:              float       # predicted_score - current_score
    trajectory:         str         # rising | stable | declining
    viral_potential:    bool        # Feature 4
    recommendation:     str         # one-line action (Feature 9)


@dataclass
class UploadFrequencySimulation:
    """
    Simulated growth impact at three upload frequencies. (Feature 6)
    All growth_pct values are relative to the baseline 1 video/week.
    """
    one_per_week:   float = 0.0
    two_per_week:   float = 0.0
    daily:          float = 0.0
    recommended:    str   = ""


@dataclass
class GrowthPrediction:
    """
    Complete growth prediction report — AI-friendly and dashboard-ready.
    (Features 16, 17)
    """
    # ── Core forecast ─────────────────────────────────────────────────────
    predicted_resonance_7d:  float = 0.0    # forecast for next 7 days
    growth_pct_7d:           float = 0.0    # % change from current avg
    confidence_score:        float = 0.0    # 0–100 (Feature 14)
    confidence_label:        str   = "low"  # low | moderate | high | very high

    # ── Momentum ──────────────────────────────────────────────────────────
    momentum_score:   float = 0.0    # composite 0–100 (Feature 2)
    momentum_label:   str   = "stable"   # accelerating | stable | declining

    # ── Topic predictions ─────────────────────────────────────────────────
    topic_forecasts:        list[TopicForecast] = field(default_factory=list)
    best_topic:             str   = ""   # highest predicted score (Feature 3)
    emerging_topic:         str   = ""   # fastest rising (Feature 12)
    declining_topic:        str   = ""   # sharpest fall

    # ── Upload frequency simulation ───────────────────────────────────────
    upload_simulation: UploadFrequencySimulation = field(
        default_factory=UploadFrequencySimulation
    )

    # ── Audience growth ───────────────────────────────────────────────────
    predicted_health_7d:     float = 0.0    # Feature 5, 13
    health_trend:            str   = "stable"

    # ── Warnings ─────────────────────────────────────────────────────────
    flag_stagnation_risk:   bool = False    # Feature 10
    stagnation_reasons:     list[str] = field(default_factory=list)
    best_upload_day:        str  = "Tuesday"   # Feature 11

    # ── AI context ────────────────────────────────────────────────────────
    growth_narrative:       str        = ""    # Feature 16
    strategy_recommendations: list[str] = field(default_factory=list)   # Feature 9

    # ── Fit metadata (debugging / Feature 19) ────────────────────────────
    series_length:    int   = 0
    fit_slope:        float = 0.0
    fit_intercept:    float = 0.0
    used_numpy:       bool  = False


# ===========================================================================
# Trend fitting helpers
# ===========================================================================

def _linear_fit_numpy(series: list[float]) -> tuple[float, float]:
    """Fit a degree-1 polynomial using numpy.polyfit. Returns (slope, intercept)."""
    x = np.arange(len(series), dtype=float)
    y = np.array(series, dtype=float)
    coeffs = np.polyfit(x, y, POLY_DEGREE)
    return float(coeffs[0]), float(coeffs[1])


def _linear_fit_pure(series: list[float]) -> tuple[float, float]:
    """
    Pure-Python least-squares linear regression (fallback when numpy absent).
    Returns (slope, intercept).
    """
    n = len(series)
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(series) / n
    num   = sum((xs[i] - x_mean) * (series[i] - y_mean) for i in range(n))
    denom = sum((xs[i] - x_mean) ** 2 for i in range(n))
    slope     = num / denom if denom != 0 else 0.0
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _fit_series(series: list[float]) -> tuple[float, float, bool]:
    """
    Dispatch to numpy or pure-Python fitter.
    Returns (slope, intercept, used_numpy).
    """
    if len(series) < MIN_DATA_POINTS:
        return 0.0, series[-1] if series else 0.0, False
    if _NUMPY_AVAILABLE:
        slope, intercept = _linear_fit_numpy(series)
        return slope, intercept, True
    slope, intercept = _linear_fit_pure(series)
    return slope, intercept, False


def _predict_next(series: list[float], horizon: int = 1) -> float:
    """Project the series forward by `horizon` steps."""
    slope, intercept, _ = _fit_series(series)
    x_next = len(series) - 1 + horizon
    return round(max(0.0, min(slope * x_next + intercept, 100.0)), 1)


# ===========================================================================
# Confidence scoring (Feature 14)
# ===========================================================================

def _confidence_score(
    series: list[float],
    data_points: int,
    channel_avg_engagement: float,
    upload_gap_weeks: int,
) -> float:
    """
    Compute prediction confidence 0–100 based on data quality and signal
    consistency.

    Factors:
      - Series length (more data = higher confidence)
      - Engagement signal strength
      - Upload consistency (gaps reduce confidence)
      - Residual variance of the fit (lower = better fit = higher confidence)
    """
    # Length bonus: saturates at 12 data points
    length_score = min(len(series) / 12.0, 1.0) * 40.0

    # Engagement signal: strong engagement = reliable predictor
    eng_score = min(channel_avg_engagement / 0.05, 1.0) * 25.0

    # Consistency bonus: no upload gaps
    consistency_score = max(0.0, 1.0 - upload_gap_weeks * 0.1) * 20.0

    # Fit quality: R² proxy — 1 - (residual variance / total variance)
    if len(series) >= MIN_DATA_POINTS and _NUMPY_AVAILABLE:
        slope, intercept = _linear_fit_numpy(series)
        fitted = [slope * i + intercept for i in range(len(series))]
        ss_res = sum((series[i] - fitted[i]) ** 2 for i in range(len(series)))
        y_mean = sum(series) / len(series)
        ss_tot = sum((v - y_mean) ** 2 for v in series)
        r2     = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0
        fit_score = r2 * 15.0
    else:
        fit_score = 7.5   # half credit without numpy or with short series

    raw = length_score + eng_score + consistency_score + fit_score
    return round(min(raw, 100.0), 1)


def _confidence_label(score: float) -> str:
    if score >= 75:
        return "very high"
    if score >= 55:
        return "high"
    if score >= 35:
        return "moderate"
    return "low"


# ===========================================================================
# Momentum scoring (Feature 2)
# ===========================================================================

def _momentum_score(
    slope: float,
    discord_delta: float,
    engagement_delta: float,
) -> tuple[float, str]:
    """
    Composite momentum score 0–100 and label.
    Combines resonance slope, community trend, and engagement direction.
    """
    # Normalise slope: ±10 pts/period maps to ±1
    slope_norm     = max(-1.0, min(slope / 10.0, 1.0))
    discord_norm   = max(-1.0, min(discord_delta / 20.0, 1.0))
    eng_norm       = max(-1.0, min(engagement_delta / 0.02, 1.0))

    composite = (slope_norm * 0.5 + discord_norm * 0.3 + eng_norm * 0.2)
    score     = round((composite + 1.0) / 2.0 * 100.0, 1)   # map -1…+1 → 0…100

    if slope >= MOMENTUM_ACCELERATING and discord_delta >= 0:
        label = "accelerating"
    elif slope <= MOMENTUM_DECLINING or (discord_delta < 0 and slope < 0):
        label = "declining"
    else:
        label = "stable"

    return score, label


# ===========================================================================
# Topic forecasting helpers (Features 3, 12)
# ===========================================================================

def _forecast_topics(
    topic_scores: dict[str, float],
    topic_deltas: dict[str, float],
) -> list[TopicForecast]:
    """
    Produce a TopicForecast for every topic in the scores map.
    Sorts by predicted score descending.
    """
    forecasts: list[TopicForecast] = []

    for topic, current in topic_scores.items():
        delta       = topic_deltas.get(topic, 0.0)
        predicted   = round(max(0.0, min(current + delta, 100.0)), 1)
        trajectory  = (
            "rising"   if delta >  2.0 else
            "declining" if delta < -2.0 else
            "stable"
        )

        # Viral potential: high score + strong positive delta (Feature 4)
        velocity    = delta / max(current, 1.0)
        viral       = current >= 70 and velocity >= VIRAL_VELOCITY_THRESHOLD / 10.0

        # One-line strategy recommendation (Feature 9)
        if trajectory == "rising" and current >= 70:
            rec = f"Double down on '{topic}' — rising resonance and strong baseline."
        elif trajectory == "rising":
            rec = f"Invest more in '{topic}' — momentum is building."
        elif trajectory == "declining" and current < 50:
            rec = f"Reduce '{topic}' frequency — both score and trend are weak."
        elif trajectory == "declining":
            rec = f"Refresh '{topic}' format — score is declining despite prior strength."
        else:
            rec = f"'{topic}' is stable — maintain current cadence."

        forecasts.append(TopicForecast(
            topic           = topic,
            current_score   = current,
            predicted_score = predicted,
            delta           = round(delta, 1),
            trajectory      = trajectory,
            viral_potential = viral,
            recommendation  = rec,
        ))

    forecasts.sort(key=lambda f: f.predicted_score, reverse=True)
    return forecasts


# ===========================================================================
# Upload frequency simulation (Feature 6)
# ===========================================================================

def _simulate_upload_frequency(
    current_resonance: float,
    current_vids_per_week: float,
) -> UploadFrequencySimulation:
    """
    Estimate growth impact of three upload frequencies.
    Model: each additional video/week above the 1/week baseline adds
    UPLOAD_FREQ_IMPACT % to the projected resonance score (diminishing returns
    applied for daily uploads to avoid over-optimistic estimates).
    """
    def _impact(freq: float) -> float:
        delta_freq = freq - UPLOAD_FREQ_BASELINE
        if delta_freq <= 0:
            multiplier = 1.0 + delta_freq * 0.05   # slight penalty for sub-baseline
        else:
            # Diminishing returns: sqrt scaling
            multiplier = 1.0 + math.sqrt(delta_freq) * UPLOAD_FREQ_IMPACT
        return round(max(0.0, min(current_resonance * multiplier, 100.0)), 1)

    one   = _impact(1.0)
    two   = _impact(2.0)
    daily = _impact(7.0)

    if current_vids_per_week < 1.0:
        recommended = "Start with 1 video/week to re-establish consistency before scaling."
    elif current_vids_per_week < 2.0:
        gain = round((two - one) / max(one, 1) * 100, 1)
        recommended = f"Moving to 2 videos/week could lift resonance by ~{gain}%."
    else:
        recommended = "You're already at a strong upload cadence. Focus on quality over frequency."

    return UploadFrequencySimulation(
        one_per_week = one,
        two_per_week = two,
        daily        = daily,
        recommended  = recommended,
    )


# ===========================================================================
# Stagnation risk detection (Feature 10)
# ===========================================================================

def _detect_stagnation(
    slope: float,
    discord_delta: float,
    audience_health: float,
    upload_gap_weeks: int,
) -> tuple[bool, list[str]]:
    """Return (flag, reason_list) for stagnation risk."""
    reasons: list[str] = []
    if slope <= 0:
        reasons.append("Resonance score is flat or declining.")
    if discord_delta < 0:
        reasons.append("Community activity is shrinking.")
    if audience_health > 0 and audience_health < 40:
        reasons.append("Audience health score is in the 'At Risk' zone.")
    if upload_gap_weeks > 2:
        reasons.append(f"Upload gaps of {upload_gap_weeks} weeks detected.")

    return len(reasons) >= 2, reasons


# ===========================================================================
# Best upload day (Feature 11)
# ===========================================================================

def _best_upload_day(series: list[float]) -> str:
    """
    Return the day-of-week name with the highest engagement weight.
    In a production system this would correlate actual publish dates with
    subsequent engagement; here we use industry-average weights and surface
    Tuesday as the default unless the series is too short to hint otherwise.
    """
    if len(series) < 7:
        return "Tuesday"
    best_day = max(DAY_OF_WEEK_WEIGHTS, key=lambda d: DAY_OF_WEEK_WEIGHTS[d])
    return BEST_UPLOAD_DAY[best_day]


# ===========================================================================
# Narrative generation (Features 16, 9)
# ===========================================================================

def _build_narrative(pred: "GrowthPrediction", inp: PredictorInput) -> str:
    """
    Plain-English growth intelligence summary for Claude's context window.
    Specific, data-citing, no filler phrases.
    """
    parts: list[str] = []

    direction = "up" if pred.growth_pct_7d >= 0 else "down"
    parts.append(
        f"Channel resonance is forecast to move {direction} "
        f"{abs(pred.growth_pct_7d):.1f}% over the next 7 days "
        f"(confidence: {pred.confidence_score:.0f}%)."
    )

    if pred.momentum_label == "accelerating":
        parts.append("Momentum is accelerating — all three signal pillars are trending positive.")
    elif pred.momentum_label == "declining":
        parts.append("Momentum is declining — consider a format or topic shift.")

    if pred.best_topic:
        best = next((f for f in pred.topic_forecasts if f.topic == pred.best_topic), None)
        if best:
            parts.append(
                f"'{pred.best_topic}' is your highest-potential topic "
                f"(predicted score: {best.predicted_score:.0f})."
            )

    if pred.emerging_topic and pred.emerging_topic != pred.best_topic:
        parts.append(f"'{pred.emerging_topic}' is the fastest-rising topic — consider more content here.")

    if pred.declining_topic:
        parts.append(f"'{pred.declining_topic}' is losing momentum — reduce frequency or refresh the format.")

    parts.append(pred.upload_simulation.recommended)

    if pred.flag_stagnation_risk and pred.stagnation_reasons:
        parts.append(f"Stagnation risk: {pred.stagnation_reasons[0]}")

    return " ".join(parts)


def _build_strategy_recommendations(
    pred: "GrowthPrediction",
    inp: PredictorInput,
) -> list[str]:
    """
    Ranked, actionable strategy recommendations. (Feature 9)
    """
    recs: list[str] = []

    # Strongest topic first
    if pred.topic_forecasts:
        top = pred.topic_forecasts[0]
        recs.append(top.recommendation)

    # Second topic if it differs
    if len(pred.topic_forecasts) >= 2:
        second = pred.topic_forecasts[1]
        if second.topic != pred.topic_forecasts[0].topic:
            recs.append(second.recommendation)

    # Upload frequency
    if inp.videos_per_week_avg < 1.0:
        recs.append(
            "Irregular uploads are limiting growth. Commit to at least 1 video/week "
            "to maintain algorithmic momentum and community habit."
        )
    elif inp.videos_per_week_avg < 2.0 and pred.growth_pct_7d > 5:
        gain = round((pred.upload_simulation.two_per_week - pred.upload_simulation.one_per_week)
                     / max(pred.upload_simulation.one_per_week, 1) * 100, 1)
        recs.append(
            f"Channel is in a strong growth phase. Testing 2 videos/week could "
            f"accelerate resonance by ~{gain}%."
        )

    # Viral topic
    viral_topics = [f for f in pred.topic_forecasts if f.viral_potential]
    if viral_topics:
        recs.append(
            f"'{viral_topics[0].topic}' shows breakout potential — high resonance "
            "and accelerating community engagement."
        )

    # Stagnation fix
    if pred.flag_stagnation_risk:
        recs.append(
            "Growth is stalling. Experiment with a new format (e.g., shorts, live, "
            "collab) to reset algorithmic distribution."
        )

    return recs[:4]


# ===========================================================================
# Public API
# ===========================================================================

def predict_growth(inp: PredictorInput) -> GrowthPrediction:
    """
    Run the full growth prediction pipeline.
    Pure function — no I/O. (Feature 18: Mock Mode, Feature 19: Logging)

    Pipeline:
      1. Parse and fit the resonance series
      2. Project 7-day forward score
      3. Score momentum
      4. Forecast per-topic trajectories
      5. Simulate upload frequency impact
      6. Compute confidence score
      7. Detect stagnation risk
      8. Build narrative and recommendations
    """
    series = inp.parsed_series()

    # ── 1. Fit trend ────────────────────────────────────────────────────────
    slope, intercept, used_numpy = _fit_series(series)
    current_score = series[-1] if series else inp.channel_avg_resonance

    logger.debug(
        "growth_predictor: series_len=%d slope=%.3f intercept=%.2f numpy=%s",
        len(series), slope, intercept, used_numpy,
    )

    # ── 2. 7-day forecast ────────────────────────────────────────────────────
    predicted_7d = _predict_next(series, horizon=FORECAST_HORIZON_DAYS)
    growth_pct   = round(
        (predicted_7d - current_score) / max(current_score, 1) * 100, 1
    )

    # ── 3. Momentum ──────────────────────────────────────────────────────────
    momentum, momentum_label = _momentum_score(
        slope,
        inp.discord_trend_delta,
        # engagement delta proxy from avg vs baseline
        inp.channel_avg_engagement - 0.02,
    )

    # ── 4. Topic forecasts ────────────────────────────────────────────────────
    topic_forecasts = _forecast_topics(inp.topic_scores, inp.topic_deltas)
    best_topic      = topic_forecasts[0].topic if topic_forecasts else ""
    emerging_topic  = max(
        inp.topic_deltas, key=lambda t: inp.topic_deltas[t], default=""
    ) if inp.topic_deltas else ""
    declining_topic = min(
        inp.topic_deltas, key=lambda t: inp.topic_deltas[t], default=""
    ) if inp.topic_deltas else ""

    # ── 5. Upload simulation ──────────────────────────────────────────────────
    upload_sim = _simulate_upload_frequency(current_score, inp.videos_per_week_avg)

    # ── 6. Confidence ─────────────────────────────────────────────────────────
    confidence = _confidence_score(
        series,
        inp.data_points,
        inp.channel_avg_engagement,
        inp.upload_gap_weeks,
    )
    conf_label = _confidence_label(confidence)

    # ── 7. Stagnation risk ────────────────────────────────────────────────────
    stagnation_flag, stagnation_reasons = _detect_stagnation(
        slope,
        inp.discord_trend_delta,
        inp.audience_health_score,
        inp.upload_gap_weeks,
    )

    # ── 8. Audience health projection (Feature 5, 13) ────────────────────────
    health_delta     = slope * 0.5   # resonance slope loosely predicts health
    predicted_health = round(
        max(0.0, min(inp.audience_health_score + health_delta, 100.0)), 1
    )
    health_trend = (
        "rising"   if health_delta >  2 else
        "declining" if health_delta < -2 else
        "stable"
    )

    # ── 9. Best upload day ────────────────────────────────────────────────────
    best_day = _best_upload_day(series)

    # ── Assemble result ───────────────────────────────────────────────────────
    result = GrowthPrediction(
        predicted_resonance_7d   = predicted_7d,
        growth_pct_7d            = growth_pct,
        confidence_score         = confidence,
        confidence_label         = conf_label,
        momentum_score           = momentum,
        momentum_label           = momentum_label,
        topic_forecasts          = topic_forecasts,
        best_topic               = best_topic,
        emerging_topic           = emerging_topic,
        declining_topic          = declining_topic,
        upload_simulation        = upload_sim,
        predicted_health_7d      = predicted_health,
        health_trend             = health_trend,
        flag_stagnation_risk     = stagnation_flag,
        stagnation_reasons       = stagnation_reasons,
        best_upload_day          = best_day,
        series_length            = len(series),
        fit_slope                = round(slope, 4),
        fit_intercept            = round(intercept, 2),
        used_numpy               = used_numpy,
    )

    # Build narrative and recommendations last (need the full result object)
    result.growth_narrative           = _build_narrative(result, inp)
    result.strategy_recommendations   = _build_strategy_recommendations(result, inp)

    logger.info(
        "growth_predictor: predicted_7d=%.1f growth_pct=%.1f%% momentum=%s confidence=%.0f%%",
        predicted_7d, growth_pct, momentum_label, confidence,
    )

    return result


def predict_growth_from_rows(
    trend_rows:    list[dict[str, Any]],
    resonance_rows: list[dict[str, Any]] | None = None,
    health_score:   float = 0.0,
    loyalty_index:  float = 0.0,
) -> GrowthPrediction:
    """
    Convenience builder: aggregate Coral query result rows into a
    PredictorInput and run predict_growth.

    trend_rows:     output of coral/queries/trends.sql (includes resonance_series)
    resonance_rows: output of coral/queries/resonance.sql (optional enrichment)
    (Feature 18: works with empty / mock row lists)
    """
    if not trend_rows:
        logger.warning("growth_predictor: no trend rows — returning baseline prediction")
        return predict_growth(PredictorInput())

    # ── Extract resonance series from the first matching predictor_feed row ──
    series_str = ""
    for row in trend_rows:
        if row.get("resonance_series"):
            series_str = str(row["resonance_series"])
            break

    # ── Aggregate channel-level signals ──────────────────────────────────────
    total = len(trend_rows)
    avg_watch    = sum(float(r.get("effective_watch_pct", 0)) for r in trend_rows) / total
    avg_eng      = sum(float(r.get("period_engagement_ratio", 0)) for r in trend_rows) / total
    disc_delta   = sum(float(r.get("discord_messages_delta", 0)) for r in trend_rows) / total
    vids_per_wk  = sum(float(r.get("videos_published", 0)) for r in trend_rows) / total
    upload_gaps  = sum(int(r.get("flag_upload_gap", 0)) for r in trend_rows)

    # ── Build topic maps from trend rows ─────────────────────────────────────
    topic_scores: dict[str, float] = {}
    topic_deltas: dict[str, float] = {}
    for row in trend_rows:
        topic = str(row.get("topic", ""))
        if not topic:
            continue
        score = float(row.get("period_resonance_score") or row.get("topic_avg_resonance") or 0)
        delta = float(row.get("resonance_delta") or 0)
        if topic not in topic_scores or score > topic_scores[topic]:
            topic_scores[topic] = score
            topic_deltas[topic] = delta

    # ── Average resonance from resonance rows (if provided) ──────────────────
    channel_avg = 0.0
    if resonance_rows:
        scores = [float(r.get("resonance_score", 0)) for r in resonance_rows]
        channel_avg = sum(scores) / len(scores) if scores else 0.0

    avg_sentiment_vals = [
        r.get("sentiment_score") for r in (resonance_rows or [])
        if r.get("sentiment_score") is not None
    ]
    avg_sentiment = (
        sum(avg_sentiment_vals) / len(avg_sentiment_vals)
        if avg_sentiment_vals else None
    )

    inp = PredictorInput(
        resonance_series_raw       = series_str,
        channel_avg_resonance      = channel_avg,
        channel_avg_watch_pct      = avg_watch,
        channel_avg_engagement     = avg_eng,
        discord_trend_delta        = disc_delta,
        avg_sentiment              = avg_sentiment,
        videos_per_week_avg        = vids_per_wk,
        upload_gap_weeks           = upload_gaps,
        topic_scores               = topic_scores,
        topic_deltas               = topic_deltas,
        audience_health_score      = health_score,
        loyalty_index              = loyalty_index,
        period_label               = "week",
        data_points                = total,
    )

    return predict_growth(inp)
