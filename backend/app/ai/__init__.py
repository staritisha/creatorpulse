"""
ai — LLM client, prompt templates, signal detectors, and insight pipeline.

Module layout
─────────────
llm_client      LLMClient singleton + LLMRequest/LLMResponse dataclasses
prompts         System prompt, intent classification, context builders
detectors       Rule-based signal detection (underperformers, spikes, risk)
insight_engine  Orchestration layer: fetch → score → detect → Claude → respond
recommendations Recommendation engine: prioritised action cards from scoring data

Typical usage:
    from ai.llm_client    import llm_client, LLMRequest
    from ai.prompts       import classify_intent, build_analytics_context
    from ai.detectors     import run_detection, DetectionReport
    from ai.insight_engine import run_insight, run_batch_insights, get_dashboard_context
    from ai.recommendations import build_recommendations, MOCK_RECOMMENDATION_SET
"""
from ai.llm_client import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    ConversationTurn,
    ConversationMemory,
    llm_client,
)
from ai.prompts import (
    INTENT_GROWTH,
    INTENT_UNDERPERFORMANCE,
    INTENT_RECOMMENDATION,
    INTENT_RESONANCE,
    INTENT_AUDIENCE_HEALTH,
    INTENT_GROWTH_FORECAST,
    INTENT_GENERAL_CHAT,
    INTENT_DEMO,
    SYSTEM_PROMPT,
    classify_intent,
    build_analytics_context,
    build_resonance_context,
    build_underperformance_context,
    CONTENT_RECOMMENDATION_PROMPT,
    UNDERPERFORMANCE_DIAGNOSIS_PROMPT,
    RESONANCE_EXPLANATION_PROMPT,
    AUDIENCE_HEALTH_PROMPT,
    GROWTH_STRATEGY_PROMPT,
    GROWTH_FORECAST_PROMPT,
)
from ai.detectors import (
    VideoSignal,
    ChannelSignal,
    DetectionReport,
    detect_video_signals,
    detect_channel_signals,
    detect_intent,
    build_recommendation_triggers,
    build_detector_context,
    run_detection,
    score_sentiment_from_messages,
)
from ai.insight_engine import (
    InsightResponse,
    InsightContext,
    run_insight,
    stream_insight,
    run_batch_insights,
    get_dashboard_context,
)
from ai.recommendations import (
    RecommendationCategory,
    Recommendation,
    RecommendationSet,
    build_recommendations,
    MOCK_RECOMMENDATION_SET,
)

__all__ = [
    # llm_client
    "LLMClient", "LLMRequest", "LLMResponse", "ConversationTurn",
    "ConversationMemory", "llm_client",
    # prompts
    "INTENT_GROWTH", "INTENT_UNDERPERFORMANCE", "INTENT_RECOMMENDATION",
    "INTENT_RESONANCE", "INTENT_AUDIENCE_HEALTH", "INTENT_GROWTH_FORECAST",
    "INTENT_GENERAL_CHAT", "INTENT_DEMO", "SYSTEM_PROMPT",
    "classify_intent", "build_analytics_context", "build_resonance_context",
    "build_underperformance_context", "CONTENT_RECOMMENDATION_PROMPT",
    "UNDERPERFORMANCE_DIAGNOSIS_PROMPT", "RESONANCE_EXPLANATION_PROMPT",
    "AUDIENCE_HEALTH_PROMPT", "GROWTH_STRATEGY_PROMPT", "GROWTH_FORECAST_PROMPT",
    # detectors
    "VideoSignal", "ChannelSignal", "DetectionReport",
    "detect_video_signals", "detect_channel_signals", "detect_intent",
    "build_recommendation_triggers", "build_detector_context",
    "run_detection", "score_sentiment_from_messages",
    # insight_engine
    "InsightResponse", "InsightContext",
    "run_insight", "stream_insight", "run_batch_insights", "get_dashboard_context",
    # recommendations
    "RecommendationCategory", "Recommendation", "RecommendationSet",
    "build_recommendations", "MOCK_RECOMMENDATION_SET",
]