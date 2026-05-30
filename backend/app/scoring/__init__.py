"""
scoring — algorithmic scoring engines for CreatorPulse.

Three independent, dependency-free modules:

* resonance_score   — per-video Resonance Score (0–100)
* audience_health   — channel Audience Health Score (0–100)
* growth_predictor  — 7-day resonance growth forecast

Typical usage:
    from scoring.resonance_score import score_video, score_batch_from_rows, ResonanceInput
    from scoring.audience_health import score_audience_health, AudienceHealthInput
    from scoring.growth_predictor import predict_growth, predict_growth_from_rows, PredictorInput
"""
from scoring.resonance_score import (
    ResonanceInput,
    ScoreBreakdown,
    ResonanceResult,
    BatchScoreResult,
    score_video,
    score_batch,
    score_from_row,
    score_batch_from_rows,
)
from scoring.audience_health import (
    AudienceHealthInput,
    HealthPillarScores,
    AudienceHealthResult,
    score_audience_health,
    score_audience_health_from_rows,
)
from scoring.growth_predictor import (
    PredictorInput,
    TopicForecast,
    UploadFrequencySimulation,
    GrowthPrediction,
    predict_growth,
    predict_growth_from_rows,
)

__all__ = [
    # resonance_score
    "ResonanceInput", "ScoreBreakdown", "ResonanceResult", "BatchScoreResult",
    "score_video", "score_batch", "score_from_row", "score_batch_from_rows",
    # audience_health
    "AudienceHealthInput", "HealthPillarScores", "AudienceHealthResult",
    "score_audience_health", "score_audience_health_from_rows",
    # growth_predictor
    "PredictorInput", "TopicForecast", "UploadFrequencySimulation", "GrowthPrediction",
    "predict_growth", "predict_growth_from_rows",
]
