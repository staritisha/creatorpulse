"""
models — Pydantic response models and dataclass insight models.

Typical usage:
    from models.response_models import APIResponse, InsightCard, RecommendationModel
    from models.insight_models import InsightType, DetectorResult, DashboardInsight
"""
from models.response_models import (
    APIResponse,
    ErrorResponse,
    ConfidenceScore,
    RecommendationModel,
    ChatResponse,
    TrendModel,
    UnderperformerModel,
    AudienceHealthModel,
    GrowthPredictionModel,
    VideoAnalyticsModel,
    AnalyticsResponse,
    InsightCard,
    InsightResponse,
    ServiceStatus,
    HealthResponse,
    SourceStatus,
    PaginatedResponse,
    RequestMetadata,
)
from models.insight_models import (
    InsightType,
    InsightPriority,
    RiskSeverity,
    MomentumDirection,
    ConfidenceModel,
    InsightObject,
    RecommendationInsight,
    RiskSignal,
    OpportunitySignal,
    ContentInsight,
    AudienceInsight,
    GrowthPredictionInsight,
    ResonanceInsight,
    UnderperformanceInsight,
    TrendInsight,
    DetectorResult,
    AIContextModel,
    ChatInsight,
    DashboardInsight,
)

__all__ = [
    # response_models
    "APIResponse", "ErrorResponse", "ConfidenceScore", "RecommendationModel",
    "ChatResponse", "TrendModel", "UnderperformerModel", "AudienceHealthModel",
    "GrowthPredictionModel", "VideoAnalyticsModel", "AnalyticsResponse",
    "InsightCard", "InsightResponse", "ServiceStatus", "HealthResponse",
    "SourceStatus", "PaginatedResponse", "RequestMetadata",
    # insight_models
    "InsightType", "InsightPriority", "RiskSeverity", "MomentumDirection",
    "ConfidenceModel", "InsightObject", "RecommendationInsight", "RiskSignal",
    "OpportunitySignal", "ContentInsight", "AudienceInsight",
    "GrowthPredictionInsight", "ResonanceInsight", "UnderperformanceInsight",
    "TrendInsight", "DetectorResult", "AIContextModel", "ChatInsight",
    "DashboardInsight",
]
