"""
routes/insights.py
CreatorPulse · AI Intelligence API Gateway

Role: Generate and expose smart creator insights — the biggest hackathon
      wow-factor API layer. While analytics.py returns metrics, this file
      returns intelligence: Claude-enhanced explanations, prioritised
      opportunities, risk alerts, and actionable recommendations.

Endpoints:
  GET  /api/insights/top                — highest-priority AI insight cards
  GET  /api/insights/recommendations    — ranked creator action plan
  GET  /api/insights/opportunities      — growth opportunity signals
  GET  /api/insights/risks              — risk alert signals
  GET  /api/insights/underperformers    — weak content diagnosis with Claude
  GET  /api/insights/audience           — audience intelligence
  GET  /api/insights/growth             — growth reasoning + forecast
  POST /api/insights/generate           — full batch intelligence pipeline
  GET  /api/insights/dashboard          — all four dashboard cards in one call
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from models.response_models import (
    APIResponse,
    InsightCard,
    InsightResponse,
    RecommendationModel,
    RequestMetadata,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/insights", tags=["insights"])

# ---------------------------------------------------------------------------
# TTL insight cache (Feature 15) — keyed on channel_id + endpoint
# Claude calls are expensive; 3-minute TTL is safe for demo.
# ---------------------------------------------------------------------------

_INSIGHT_CACHE_TTL_S: int = 180

_insight_cache: dict[str, tuple[Any, float]] = {}


def _icache_key(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def _icache_get(key: str) -> Any | None:
    entry = _insight_cache.get(key)
    if entry and (time.time() - entry[1]) < _INSIGHT_CACHE_TTL_S:
        return entry[0]
    return None


def _icache_set(key: str, value: Any) -> None:
    _insight_cache[key] = (value, time.time())


# ---------------------------------------------------------------------------
# Mock insight set (Feature 16: Demo Mode Support)
# ---------------------------------------------------------------------------

_MOCK_TOP_INSIGHTS: list[dict[str, Any]] = [
    {
        "type":        "viral_signal",
        "title":       "🔥 Breakout opportunity: LangGraph tutorial",
        "summary":     "LangGraph tutorial triggered a 4.1× Discord spike with 91 resonance",
        "key_insight": (
            "Your LangGraph Deep Dive drove 4.1× more Discord discussion than your channel baseline "
            "with 91 resonance and 66% retention — both above breakout thresholds. "
            "Follow-up content within 72 hours captures peak algorithmic momentum."
        ),
        "signals":     ["Discord spike: 4.1×", "Resonance: 91/100", "Retention: 66%"],
        "recommendation": "Publish a follow-up LangGraph video this week and announce it in Discord today",
        "confidence":  91.0, "confidence_label": "high", "from_mock": True,
    },
    {
        "type":        "top_opportunity",
        "title":       "AI Agents is your #1 growth lever",
        "summary":     "AI Agents averages 87 resonance — 31 pts above your channel average",
        "key_insight": (
            "AI Agent tutorials generate 3.2× more Discord messages than career content "
            "and maintain 68% average retention. This is your highest-signal content category "
            "and your fastest path to channel growth."
        ),
        "signals":     ["AI Agents resonance: 87/100", "Channel avg: 56/100", "Discord: 3.2× spike ratio"],
        "recommendation": "Publish 2 AI Agent tutorials in the next 3 weeks",
        "confidence":  88.0, "confidence_label": "high", "from_mock": True,
    },
    {
        "type":        "underperformance",
        "title":       "Career Q&A #12: false popularity detected",
        "summary":     "180k views but only 22% retention and 3 Discord messages",
        "key_insight": (
            "'Career Q&A #12' attracted high clicks but viewers left quickly — "
            "a title-content mismatch. The thumbnail promised fast answers but the "
            "intro was too slow to deliver."
        ),
        "signals":     ["Views: 180k", "Retention: 22%", "Discord msgs: 3", "Diagnosis: ctr_retention_mismatch"],
        "recommendation": "Deliver the core answer within the first 45 seconds",
        "confidence":  82.0, "confidence_label": "high", "from_mock": True,
    },
]

_MOCK_RISKS: list[dict[str, Any]] = [
    {
        "risk_type": "topic_decay", "label": "Topic Decay",
        "severity": "high",
        "reason":   "Career Advice resonance delta: -6.2 pts this period",
        "mitigation": "Angle Career Advice content toward your AI niche or reduce upload frequency",
        "confidence": 78.0,
    },
    {
        "risk_type": "upload_consistency", "label": "Upload Consistency",
        "severity": "medium",
        "reason":   "Upload gaps detected in 1 recent period",
        "mitigation": "Commit to 1 video/week every Tuesday for 4 weeks",
        "confidence": 65.0,
    },
]

_MOCK_OPPORTUNITIES: list[dict[str, Any]] = [
    {
        "opportunity_type": "viral_window", "label": "Viral Window",
        "impact":    "high",
        "signals":   ["Discord spike: 4.1× baseline", "Resonance: 91/100", "Action window: 72h"],
        "action":    "Publish a follow-up LangGraph video immediately",
        "topic":     "AI Agents",
        "time_sensitive": True,
        "window_hours": 72,
        "confidence": 89.0,
    },
    {
        "opportunity_type": "topic_growth", "label": "Topic Growth",
        "impact":    "high",
        "signals":   ["Resonance delta: +8.5 pts", "Avg resonance: 87/100"],
        "action":    "Increase AI Agents upload frequency over the next 4 weeks",
        "topic":     "AI Agents",
        "time_sensitive": False,
        "confidence": 85.0,
    },
]


# ---------------------------------------------------------------------------
# Shared pipeline helper — fetches data + runs scoring for all endpoints
# ---------------------------------------------------------------------------

def _get_pipeline_context(channel_id: str, mock_mode: bool) -> dict[str, Any]:
    """
    Run the data + scoring layers without calling Claude.
    Returns a dict with resonance_rows, trend_rows, health_data, forecast_data.
    Falls back to analytics.py mock data if services are unavailable.
    """
    try:
        from ai.insight_engine import get_dashboard_context  # type: ignore[import]
        return get_dashboard_context(channel_id=channel_id, mock_mode=mock_mode)
    except Exception as exc:
        logger.debug("insights: pipeline context fallback (%s)", exc)
        from routes.analytics import (  # type: ignore[import]
            _MOCK_RESONANCE_ROWS, _MOCK_TREND_ROWS, _MOCK_HEALTH_DATA, _MOCK_FORECAST_DATA,
        )
        return {
            "channel_avg_resonance": 65.0,
            "top_topic":             "AI Agents",
            "data_points":           9,
            "resonance_videos":      _MOCK_RESONANCE_ROWS,
            "growth_forecast":       _MOCK_FORECAST_DATA,
            "audience_health":       _MOCK_HEALTH_DATA,
        }


# ===========================================================================
# GET /api/insights/top  (Features 4, 11, 13)
# ===========================================================================

@router.get("/top", response_model=APIResponse)
async def get_top_insights(
    channel_id: str  = Query(default="demo"),
    mock_mode:  bool = Query(default=True),
    limit:      int  = Query(default=4, ge=1, le=10),
) -> APIResponse:
    """
    Highest-priority AI insight cards for the dashboard.
    Runs the full batch pipeline or returns cached results.
    (Feature 4: Top Insights Endpoint + Feature 13: Dashboard Insight Cards)
    """
    t0  = time.time()
    key = _icache_key("top", channel_id, str(mock_mode))

    if cached := _icache_get(key):
        return APIResponse.ok(data=cached, message="Top insights (cached)",
                              metadata={"from_cache": True, "latency_ms": 0})

    if mock_mode:
        payload = {
            "insights":    _MOCK_TOP_INSIGHTS[:limit],
            "count":       min(limit, len(_MOCK_TOP_INSIGHTS)),
            "top_summary": "AI Agents is your highest-leverage content — 31 pts above channel average",
        }
        _icache_set(key, payload)
        return APIResponse.ok(data=payload, message="Top insights loaded (demo)",
                              metadata={"from_mock": True, "latency_ms": int((time.time() - t0) * 1000)})

    try:
        from ai.insight_engine import run_batch_insights  # type: ignore[import]
        batch = run_batch_insights(channel_id=channel_id, mock_mode=mock_mode)
        cards = []
        for insight_type, ir in batch.items():
            card = InsightCard.from_insight_response(ir, insight_type).model_dump()
            cards.append(card)

        # Feature 11: Prioritise — viral first, then by confidence
        priority_order = ["viral_signal", "top_opportunity", "underperformance", "audience_health", "growth_forecast"]
        cards.sort(key=lambda c: (priority_order.index(c["type"]) if c["type"] in priority_order else 99, -c.get("confidence", {}).get("confidence", 0)))
        top = cards[:limit]
        payload = {
            "insights":    top,
            "count":       len(top),
            "top_summary": top[0]["summary"] if top else "",
        }
        _icache_set(key, payload)
        latency_ms = int((time.time() - t0) * 1000)
        logger.info("insights/top: %d cards latency=%dms", len(top), latency_ms)
        return APIResponse.ok(data=payload, message="Top insights generated",
                              metadata=RequestMetadata(latency_ms=latency_ms, channel_id=channel_id,
                                                       coral_sources=["youtube","discord","google_sheets"]).to_dict())
    except Exception as exc:
        logger.error("insights/top: pipeline error (%s)", exc)
        return APIResponse.ok(
            data={"insights": _MOCK_TOP_INSIGHTS[:limit], "count": limit, "top_summary": _MOCK_TOP_INSIGHTS[0]["summary"]},
            message="Top insights (fallback)", metadata={"from_mock": True, "error": str(exc)[:80]},
        )


# ===========================================================================
# GET /api/insights/recommendations  (Feature 5)
# ===========================================================================

@router.get("/recommendations", response_model=APIResponse)
async def get_recommendations(
    channel_id: str  = Query(default="demo"),
    mock_mode:  bool = Query(default=True),
    goal:       str  = Query(default="growth"),
    limit:      int  = Query(default=5, ge=1, le=10),
) -> APIResponse:
    """
    Ranked creator action plan from recommendations.py.
    (Feature 5: Recommendation Insights Endpoint)
    """
    t0 = time.time()
    key = _icache_key("recs", channel_id, goal, str(mock_mode))
    if cached := _icache_get(key):
        return APIResponse.ok(data=cached, message="Recommendations (cached)", metadata={"from_cache": True})

    try:
        ctx = _get_pipeline_context(channel_id, mock_mode)
        from ai.recommendations import build_recommendations  # type: ignore[import]
        rec_set = build_recommendations(
            resonance_rows = ctx.get("resonance_videos", []),
            trend_rows     = [],
            health_data    = ctx.get("audience_health"),
            forecast_data  = ctx.get("growth_forecast"),
            goal           = goal,
            mock_mode      = mock_mode,
        )
        recs = [RecommendationModel.from_rec(r).model_dump() for r in rec_set.recommendations[:limit]]
        payload = {
            "recommendations": recs,
            "top_opportunity": rec_set.top_opportunity,
            "primary_risk":    rec_set.primary_risk,
            "goal":            goal,
            "count":           len(recs),
        }
    except Exception as exc:
        logger.warning("insights/recommendations: fallback (%s)", exc)
        from models.response_models import RecommendationModel as RM  # type: ignore[import]
        payload = {
            "recommendations": [RM.mock().model_dump()],
            "top_opportunity": "AI Agents content outperforms your channel by 31 resonance points",
            "primary_risk":    "Career advice content is declining",
            "goal":            goal, "count": 1,
        }

    _icache_set(key, payload)
    latency_ms = int((time.time() - t0) * 1000)
    return APIResponse.ok(data=payload, message="Recommendations generated",
                          metadata=RequestMetadata(latency_ms=latency_ms, channel_id=channel_id, from_mock=mock_mode).to_dict())


# ===========================================================================
# GET /api/insights/opportunities  (Feature 6)
# ===========================================================================

@router.get("/opportunities", response_model=APIResponse)
async def get_opportunities(
    channel_id: str  = Query(default="demo"),
    mock_mode:  bool = Query(default=True),
) -> APIResponse:
    """
    Growth opportunities: viral windows, rising topics, momentum spikes.
    (Feature 6: Growth Opportunity Endpoint)
    """
    t0 = time.time()
    if mock_mode:
        return APIResponse.ok(
            data={"opportunities": _MOCK_OPPORTUNITIES, "count": len(_MOCK_OPPORTUNITIES)},
            message="Growth opportunities loaded (demo)",
            metadata={"from_mock": True, "latency_ms": int((time.time() - t0) * 1000)},
        )

    try:
        ctx = _get_pipeline_context(channel_id, False)
        res_rows   = ctx.get("resonance_videos", [])
        from ai.detectors import run_detection  # type: ignore[import]
        dr = run_detection(question="growth opportunities", resonance_rows=res_rows)

        opps: list[dict[str, Any]] = []
        from models.insight_models import OpportunitySignal  # type: ignore[import]

        # Viral windows
        for sig in dr.video_signals:
            if sig.is_viral_candidate:
                opps.append(OpportunitySignal.viral_window(
                    sig.title, sig.spike_ratio, sig.resonance_score
                ).to_dict())

        # Rising topics
        if dr.channel_signal.emerging_topic:
            topic_scores = {r.get("topic"):float(r.get("resonance_score",0)) for r in res_rows if r.get("topic")}
            opps.append(OpportunitySignal.topic_growth(
                dr.channel_signal.emerging_topic,
                3.5,
                topic_scores.get(dr.channel_signal.emerging_topic, 70.0),
            ).to_dict())

        payload = {"opportunities": opps, "count": len(opps)}
    except Exception as exc:
        logger.warning("insights/opportunities: fallback (%s)", exc)
        payload = {"opportunities": _MOCK_OPPORTUNITIES, "count": len(_MOCK_OPPORTUNITIES)}

    latency_ms = int((time.time() - t0) * 1000)
    return APIResponse.ok(data=payload, message="Growth opportunities loaded",
                          metadata=RequestMetadata(latency_ms=latency_ms, channel_id=channel_id).to_dict())


# ===========================================================================
# GET /api/insights/risks  (Feature 7)
# ===========================================================================

@router.get("/risks", response_model=APIResponse)
async def get_risks(
    channel_id: str  = Query(default="demo"),
    mock_mode:  bool = Query(default=True),
) -> APIResponse:
    """
    Risk alert signals: audience fatigue, retention decline, topic decay.
    (Feature 7: Risk Detection Endpoint)
    """
    t0 = time.time()
    if mock_mode:
        return APIResponse.ok(
            data={"risks": _MOCK_RISKS, "count": len(_MOCK_RISKS), "stagnation_risk": False},
            message="Risk signals loaded (demo)",
            metadata={"from_mock": True, "latency_ms": int((time.time() - t0) * 1000)},
        )

    try:
        ctx = _get_pipeline_context(channel_id, False)
        res_rows = ctx.get("resonance_videos", [])
        from ai.detectors import run_detection  # type: ignore[import]
        dr = run_detection(question="risks", resonance_rows=res_rows)
        from models.insight_models import RiskSignal  # type: ignore[import]

        risks: list[dict[str, Any]] = []
        for rs in dr.channel_signal.risk_signals:
            risks.append({"risk_type": rs.replace(" ","_"), "label": rs, "severity": "medium",
                          "reason": rs, "mitigation": "", "confidence": 70.0})
        if dr.channel_signal.audience_fatigue_flag:
            risks.append(RiskSignal.audience_fatigue().to_dict())
        if dr.channel_signal.declining_topic:
            risks.append(RiskSignal.topic_decay(dr.channel_signal.declining_topic, -4.0).to_dict())

        payload = {
            "risks":           risks,
            "count":           len(risks),
            "stagnation_risk": dr.channel_signal.stagnation_risk,
        }
    except Exception as exc:
        logger.warning("insights/risks: fallback (%s)", exc)
        payload = {"risks": _MOCK_RISKS, "count": len(_MOCK_RISKS), "stagnation_risk": False}

    latency_ms = int((time.time() - t0) * 1000)
    return APIResponse.ok(data=payload, message="Risk signals loaded",
                          metadata=RequestMetadata(latency_ms=latency_ms, channel_id=channel_id).to_dict())


# ===========================================================================
# GET /api/insights/underperformers  (Feature 8)
# ===========================================================================

@router.get("/underperformers", response_model=APIResponse)
async def get_underperformer_insights(
    channel_id: str  = Query(default="demo"),
    mock_mode:  bool = Query(default=True),
    with_claude: bool = Query(default=False, description="Enhance diagnosis with Claude explanation"),
) -> APIResponse:
    """
    Underperformer diagnosis enriched with Claude reasoning if requested.
    (Feature 8: Underperformance Diagnosis Endpoint + Feature 12: Claude Enhancement)
    """
    t0 = time.time()
    ctx = _get_pipeline_context(channel_id, mock_mode)
    res_rows = ctx.get("resonance_videos", [])
    weak = [r for r in res_rows if float(r.get("resonance_score", 100)) < 50]

    from models.insight_models import UnderperformanceInsight  # type: ignore[import]
    diagnoses = [UnderperformanceInsight.from_row(r).to_dict() for r in weak]

    # Feature 12: Optional Claude enhancement for richer explanation
    claude_explanation = ""
    if with_claude and not mock_mode and diagnoses:
        try:
            from ai.insight_engine import run_insight  # type: ignore[import]
            ir = run_insight(
                question   = "Why are my underperforming videos failing and what is the most important fix?",
                channel_id = channel_id,
                intent     = "underperformance_diagnosis",
                mock_mode  = False,
            )
            claude_explanation = ir.key_insight
        except Exception as exc:
            logger.debug("insights/underperformers: Claude enhancement skipped (%s)", exc)

    payload = {
        "underperformers":    diagnoses,
        "count":              len(diagnoses),
        "claude_explanation": claude_explanation,
    }
    latency_ms = int((time.time() - t0) * 1000)
    return APIResponse.ok(data=payload, message="Underperformer insights loaded",
                          metadata=RequestMetadata(latency_ms=latency_ms, channel_id=channel_id,
                                                   from_mock=mock_mode).to_dict())


# ===========================================================================
# GET /api/insights/audience  (Feature 9)
# ===========================================================================

@router.get("/audience", response_model=APIResponse)
async def get_audience_insights(
    channel_id: str  = Query(default="demo"),
    mock_mode:  bool = Query(default=True),
) -> APIResponse:
    """
    Audience intelligence: loyalty, engagement quality, sentiment, health.
    (Feature 9: Audience Intelligence Endpoint)
    """
    t0  = time.time()
    ctx = _get_pipeline_context(channel_id, mock_mode)
    health = ctx.get("audience_health", {})

    from models.insight_models import AudienceInsight  # type: ignore[import]
    insight = AudienceInsight.from_health_dict(health)

    # Build an InsightObject for the summary card
    from models.insight_models import InsightObject, InsightType, InsightPriority, ConfidenceModel  # type: ignore[import]
    obj = InsightObject(
        title        = f"Audience health: {insight.health_score:.0f}/100 ({insight.loyalty} loyalty)",
        summary      = insight.insight_summary,
        insight_type = InsightType.AUDIENCE_HEALTH,
        priority     = InsightPriority.HIGH if insight.flag_burnout or insight.flag_passive else InsightPriority.MEDIUM,
        confidence   = ConfidenceModel.moderate("Derived from engagement, retention, and Discord signals"),
        recommendation = (
            "Introduce interactive content to re-engage passive viewers"
            if insight.flag_passive else
            "Maintain current community cadence — audience health is strong"
        ),
    )

    payload = {
        "audience":      insight.to_dict(),
        "insight_card":  obj.to_dict(),
    }
    latency_ms = int((time.time() - t0) * 1000)
    return APIResponse.ok(data=payload, message="Audience insights loaded",
                          metadata=RequestMetadata(latency_ms=latency_ms, channel_id=channel_id, from_mock=mock_mode).to_dict())


# ===========================================================================
# GET /api/insights/growth  (Feature 10)
# ===========================================================================

@router.get("/growth", response_model=APIResponse)
async def get_growth_insights(
    channel_id: str  = Query(default="demo"),
    mock_mode:  bool = Query(default=True),
) -> APIResponse:
    """
    Growth reasoning: forecast, momentum drivers, risk, strategy.
    (Feature 10: Growth Intelligence Endpoint)
    """
    t0  = time.time()
    ctx = _get_pipeline_context(channel_id, mock_mode)
    forecast = ctx.get("growth_forecast", {})

    from models.insight_models import (  # type: ignore[import]
        GrowthPredictionInsight, InsightObject, InsightType, InsightPriority, ConfidenceModel,
    )
    gpi = GrowthPredictionInsight.from_dict(forecast)
    obj = InsightObject(
        title        = f"Growth momentum: {gpi.momentum_label.value} ({gpi.growth_pct_7d:+.1f}% / 7d)",
        summary      = (
            f"Your channel momentum is {gpi.momentum_label.value}. "
            f"Best topic: '{gpi.best_topic}'. "
            + (f"Declining: '{gpi.declining_topic}'." if gpi.declining_topic else "")
        ),
        insight_type = InsightType.GROWTH_OPPORTUNITY,
        priority     = InsightPriority.HIGH if gpi.momentum_label.value == "accelerating" else InsightPriority.MEDIUM,
        confidence   = gpi.confidence,
        reasoning    = " | ".join(gpi.drivers) if gpi.drivers else "",
        evidence     = gpi.drivers,
        recommendation = gpi.upload_rec or f"Focus upcoming uploads on '{gpi.best_topic}'",
    )

    payload = {
        "growth":       gpi.to_dict(),
        "insight_card": obj.to_dict(),
    }
    latency_ms = int((time.time() - t0) * 1000)
    return APIResponse.ok(data=payload, message="Growth insights loaded",
                          metadata=RequestMetadata(latency_ms=latency_ms, channel_id=channel_id, from_mock=mock_mode).to_dict())


# ===========================================================================
# POST /api/insights/generate  (Features 2, 3, 14)
# ===========================================================================

class GenerateInsightRequest(BaseModel):
    channel_id:  str  = Field(default="demo")
    goal:        str  = Field(default="growth")
    mock_mode:   bool = Field(default=True)
    demo_mode:   bool = Field(default=True)
    insight_types: list[str] = Field(
        default=["top_opportunity", "underperformance", "audience_health", "growth_forecast"],
        description="Subset of insight types to generate",
    )


@router.post("/generate", response_model=APIResponse)
async def generate_insights(body: GenerateInsightRequest) -> APIResponse:
    """
    Full batch intelligence pipeline — one request generates all insight types.
    Uses insight_engine.run_batch_insights() for a single-Coral-pass architecture.
    (Feature 14: Batch Insight Generation + Feature 2: Creator Insight Generation)
    """
    t0  = time.time()
    key = _icache_key("generate", body.channel_id, body.goal, str(body.mock_mode))
    if cached := _icache_get(key):
        return APIResponse.ok(data=cached, message="Batch insights (cached)", metadata={"from_cache": True})

    try:
        from ai.insight_engine import run_batch_insights  # type: ignore[import]
        batch = run_batch_insights(
            channel_id = body.channel_id,
            goal       = body.goal,
            mock_mode  = body.mock_mode,
        )
        cards: list[dict[str, Any]] = []
        all_recs: list[dict[str, Any]] = []
        for insight_type, ir in batch.items():
            if insight_type not in body.insight_types:
                continue
            card = InsightCard.from_insight_response(ir, insight_type).model_dump()
            cards.append(card)
            if ir.recommendations:
                for rec in ir.recommendations.recommendations[:2]:
                    try:
                        all_recs.append(RecommendationModel.from_rec(rec).model_dump())
                    except Exception:
                        pass

        # De-duplicate recommendations by title
        seen_titles: set[str] = set()
        unique_recs = [r for r in all_recs if not (r["title"] in seen_titles or seen_titles.add(r["title"]))]  # type: ignore[func-returns-value]

        payload = InsightResponse(
            summary         = cards[0]["summary"] if cards else "",
            top_insights    = [InsightCard(**c) for c in cards],
            recommendations = [RecommendationModel(**r) for r in unique_recs[:5]],
            risks           = [s for c in cards for s in c.get("signals", []) if "⚠" in s or "risk" in s.lower()],
            opportunities   = [s for c in cards for s in c.get("signals", []) if "📈" in s or "🔥" in s],
            from_mock       = body.mock_mode,
            latency_ms      = int((time.time() - t0) * 1000),
        ).model_dump()

        _icache_set(key, payload)
        latency_ms = int((time.time() - t0) * 1000)
        logger.info("insights/generate: %d cards %d recs latency=%dms", len(cards), len(unique_recs), latency_ms)
        return APIResponse.ok(data=payload, message="Batch insights generated",
                              metadata=RequestMetadata(latency_ms=latency_ms, channel_id=body.channel_id,
                                                       from_mock=body.mock_mode,
                                                       coral_sources=["youtube","discord","google_sheets"]).to_dict())

    except Exception as exc:
        logger.error("insights/generate: error (%s)", exc)
        fallback = InsightResponse.mock().model_dump()
        return APIResponse.ok(data=fallback, message="Batch insights (fallback)",
                              metadata={"from_mock": True, "error": str(exc)[:100]})


# ===========================================================================
# GET /api/insights/dashboard  (Feature 13: Dashboard Insight Cards)
# ===========================================================================

@router.get("/dashboard", response_model=APIResponse)
async def get_dashboard_cards(
    channel_id: str  = Query(default="demo"),
    mock_mode:  bool = Query(default=True),
) -> APIResponse:
    """
    All four dashboard AI cards in a single lightweight call.
    Uses the pre-computed pipeline context without Claude (fast load).
    (Feature 13: Dashboard Insight Cards)
    """
    t0 = time.time()
    try:
        from models.insight_models import DashboardInsight  # type: ignore[import]
        ctx      = _get_pipeline_context(channel_id, mock_mode)
        forecast = ctx.get("growth_forecast", {})
        health   = ctx.get("audience_health", {})
        res_rows = ctx.get("resonance_videos", [])

        # Find viral candidate
        viral_row = next(
            (r for r in res_rows if float(r.get("community_spike_ratio", 0)) >= 3.0
             and float(r.get("resonance_score", 0)) >= 75),
            None,
        )
        top_topic    = ctx.get("top_topic", "")
        top_res      = max((float(r.get("resonance_score", 0)) for r in res_rows if r.get("topic") == top_topic), default=0.0)
        top_delta    = float(forecast.get("growth_pct_7d", 0))
        risk_label   = "topic_decay" if forecast.get("declining_topic") else "audience_fatigue"
        risk_detail  = (
            f"'{forecast.get('declining_topic','')}' resonance is declining this period"
            if forecast.get("declining_topic")
            else "Engagement fatigue signal detected"
        )

        cards: list[dict[str, Any]] = [
            DashboardInsight.top_opportunity(top_topic, top_res, top_delta).to_dict(),
        ]
        if viral_row:
            cards.append(DashboardInsight.viral_signal(
                str(viral_row.get("title", "")),
                float(viral_row.get("community_spike_ratio", 0)),
            ).to_dict())
        cards.append(DashboardInsight.growth_risk(risk_label, risk_detail).to_dict())
        cards.append({
            "card_type": "audience_health",
            "title":     "Audience Health",
            "value":     f"{health.get('health_score', 0):.0f}/100",
            "subtitle":  health.get("health_label", "unknown"),
            "body":      f"Sentiment: {health.get('overall_sentiment','neutral')} | "
                         f"Weak signal: {', '.join(health.get('weak_signals',['none'])[:1])}",
            "badge":     "👥",
            "priority":  "medium",
            "confidence":70.0,
        })

        payload = {"cards": cards, "count": len(cards)}
    except Exception as exc:
        logger.warning("insights/dashboard: fallback (%s)", exc)
        from models.insight_models import DashboardInsight as DI  # type: ignore[import]
        payload = {"cards": [c.to_dict() for c in DI.mock_set()], "count": 4}

    latency_ms = int((time.time() - t0) * 1000)
    return APIResponse.ok(data=payload, message="Dashboard cards loaded",
                          metadata={"latency_ms": latency_ms, "from_mock": mock_mode})
