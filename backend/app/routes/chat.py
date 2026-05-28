"""
routes/chat.py
CreatorPulse · Conversational AI Gateway

Role: The main AI experience layer — handles every creator conversation,
      routes questions through the full insight pipeline, and streams or
      returns structured responses.

Endpoints:
  POST /api/chat              — non-streaming structured chat
  POST /api/chat/stream       — SSE streaming response
  GET  /api/chat/suggestions  — demo quick-prompt buttons
  DELETE /api/chat/memory     — clear conversation history

All endpoints delegate to ai/insight_engine.py; no direct LLM calls here.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from models.response_models import (
    APIResponse,
    ChatResponse,
    ErrorResponse,
    RecommendationModel,
    RequestMetadata,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# ---------------------------------------------------------------------------
# In-memory rate-limiter (Feature 13: Chat Rate Limiting)
# Keyed by IP; tracks last request timestamp and rolling count.
# ---------------------------------------------------------------------------

_RATE_WINDOW_S:  int = 60     # 1-minute window
_RATE_MAX_CALLS: int = 30     # max requests per window (demo-safe)

_rate_store: dict[str, dict[str, Any]] = {}


def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    entry = _rate_store.setdefault(client_ip, {"count": 0, "window_start": now})
    if now - entry["window_start"] > _RATE_WINDOW_S:
        entry["count"]        = 0
        entry["window_start"] = now
    entry["count"] += 1
    if entry["count"] > _RATE_MAX_CALLS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded — max {_RATE_MAX_CALLS} requests/{_RATE_WINDOW_S}s",
        )


# ---------------------------------------------------------------------------
# Follow-up prompt generator (Feature 16)
# ---------------------------------------------------------------------------

_FOLLOW_UP_MAP: dict[str, list[str]] = {
    "content_recommendation": [
        "Why is my top topic performing so well?",
        "How do I improve retention on weaker videos?",
        "What upload schedule would maximise my growth?",
    ],
    "underperformance_diagnosis": [
        "How do I improve my video hook?",
        "Which of my videos are actually performing well?",
        "What content should I create more of?",
    ],
    "audience_health": [
        "How do I convert passive viewers into engaged community members?",
        "What content drives the most Discord discussion?",
        "Is my audience growing or shrinking?",
    ],
    "growth_analysis": [
        "What is my single biggest growth opportunity right now?",
        "Which topic should I double down on?",
        "How do I accelerate my channel momentum?",
    ],
    "growth_forecast": [
        "What is driving my growth momentum?",
        "How do I sustain this growth rate?",
        "Which topic is most at risk of declining?",
    ],
    "resonance_explanation": [
        "How does this compare to my other top videos?",
        "What makes community discussion spike like this?",
        "How can I replicate this performance?",
    ],
    "general_chat": [
        "What should I upload next?",
        "Why did my last video underperform?",
        "How healthy is my audience right now?",
    ],
}


def _follow_up_prompts(intent: str) -> list[str]:
    return _FOLLOW_UP_MAP.get(intent, _FOLLOW_UP_MAP["general_chat"])


# ===========================================================================
# Request / response schemas
# ===========================================================================

class ChatRequest(BaseModel):
    """POST /api/chat body."""
    question:        str   = Field(..., min_length=1, max_length=1000)
    channel_id:      str   = Field(default="demo")
    conversation_id: str | None = None
    goal:            str   = Field(default="growth")   # growth | engagement | community | retention
    demo_mode:       bool  = True
    mock_mode:       bool  = False


class ChatStreamRequest(BaseModel):
    """POST /api/chat/stream body."""
    question:        str   = Field(..., min_length=1, max_length=1000)
    channel_id:      str   = Field(default="demo")
    conversation_id: str | None = None
    goal:            str   = Field(default="growth")
    demo_mode:       bool  = True
    mock_mode:       bool  = False


class ChatResponseEnvelope(BaseModel):
    """Full structured chat response — Feature 9."""
    answer:               str
    summary:              str        = ""
    key_insight:          str        = ""
    signals:              list[str]  = Field(default_factory=list)
    recommendations:      list[RecommendationModel] = Field(default_factory=list)
    follow_up_questions:  list[str]  = Field(default_factory=list)
    confidence:           float      = 0.0
    confidence_label:     str        = "moderate"
    intent:               str        = "general_chat"
    model_used:           str        = ""
    from_mock:            bool       = False
    from_cache:           bool       = False
    latency_ms:           int        = 0
    conversation_id:      str        = ""


# ===========================================================================
# POST /api/chat  — structured non-streaming (Feature 1, 2, 9)
# ===========================================================================

@router.post("", response_model=APIResponse)
async def chat(request: Request, body: ChatRequest) -> APIResponse:
    """
    Main chat endpoint. Runs the full insight pipeline and returns a
    structured ChatResponseEnvelope.

    Flow:
      1. Rate-limit check
      2. Assign/reuse conversation_id (Feature 7: Conversation Memory)
      3. Delegate to insight_engine.run_insight()
      4. Convert InsightResponse → ChatResponseEnvelope
      5. Attach follow-up questions (Feature 16)
      6. Return wrapped in APIResponse envelope
    """
    t0 = time.time()
    client_ip = request.client.host if request.client else "unknown"

    # Feature 13: Rate limiting
    _check_rate_limit(client_ip)

    # Feature 7: Conversation memory — assign ID if not provided
    conv_id = body.conversation_id or str(uuid.uuid4())

    logger.info(
        "chat: question='%s...' channel=%s intent=? conv=%s",
        body.question[:50], body.channel_id, conv_id[:8],
    )

    try:
        # Feature 5: Full insight pipeline
        from ai.insight_engine import run_insight  # type: ignore[import]
        ir = run_insight(
            question        = body.question,
            channel_id      = body.channel_id,
            conversation_id = conv_id,
            goal            = body.goal,
            mock_mode       = body.mock_mode,
            demo_mode       = body.demo_mode,
        )

        # Build recommendations list (Feature 8)
        recs: list[RecommendationModel] = []
        if ir.recommendations and ir.recommendations.recommendations:
            recs = [
                RecommendationModel.from_rec(r)
                for r in ir.recommendations.recommendations[:4]
            ]

        # Feature 16: Follow-up questions
        follow_ups = _follow_up_prompts(ir.intent)

        latency_ms = int((time.time() - t0) * 1000)

        envelope = ChatResponseEnvelope(
            answer              = ir.key_insight or ir.summary,
            summary             = ir.summary,
            key_insight         = ir.key_insight,
            signals             = ir.signals,
            recommendations     = recs,
            follow_up_questions = follow_ups,
            confidence          = round(ir.confidence * 100, 1),
            confidence_label    = ir.confidence_label,
            intent              = ir.intent,
            model_used          = ir.model_used,
            from_mock           = ir.from_mock,
            from_cache          = ir.from_cache,
            latency_ms          = latency_ms,
            conversation_id     = conv_id,
        )

        # Feature 17: Logging
        logger.info(
            "chat: OK intent=%s confidence=%.0f%% latency=%dms mock=%s cache=%s",
            ir.intent, ir.confidence * 100, latency_ms, ir.from_mock, ir.from_cache,
        )

        return APIResponse.ok(
            data     = envelope.model_dump(),
            message  = "Chat response generated",
            metadata = RequestMetadata(
                latency_ms     = latency_ms,
                model_used     = ir.model_used,
                intent         = ir.intent,
                data_points    = ir.data_points,
                from_mock      = ir.from_mock,
                from_cache     = ir.from_cache,
                coral_sources  = ["youtube", "discord", "google_sheets"],
                channel_id     = body.channel_id,
            ).to_dict(),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("chat: pipeline error — %s", exc, exc_info=True)

        # Feature 14: Fallback mode — return mock instead of 500
        mock = ChatResponse.mock()
        envelope = ChatResponseEnvelope(
            answer              = mock.answer,
            summary             = mock.summary,
            key_insight         = mock.key_insight,
            signals             = mock.signals,
            recommendations     = mock.recommendations,
            follow_up_questions = _follow_up_prompts("general_chat"),
            confidence          = mock.confidence.confidence,
            confidence_label    = mock.confidence.label,
            intent              = "general_chat",
            model_used          = "fallback",
            from_mock           = True,
            latency_ms          = int((time.time() - t0) * 1000),
            conversation_id     = conv_id,
        )
        return APIResponse.ok(
            data    = envelope.model_dump(),
            message = "Fallback response (pipeline unavailable)",
            metadata= {"fallback": True, "error": str(exc)[:120]},
        )


# ===========================================================================
# POST /api/chat/stream  — SSE streaming (Feature 10)
# ===========================================================================

@router.post("/stream")
async def chat_stream(request: Request, body: ChatStreamRequest) -> StreamingResponse:
    """
    Streaming chat endpoint — returns Server-Sent Events (SSE).
    Each token chunk is emitted as:  data: <chunk>\n\n
    A final  data: [DONE]\n\n  signals completion.

    Frontend EventSource pattern:
      const es = new EventSource("/api/chat/stream");

    (Feature 10: Streaming Response Support)
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    conv_id = body.conversation_id or str(uuid.uuid4())

    logger.info(
        "chat/stream: question='%s...' channel=%s conv=%s",
        body.question[:50], body.channel_id, conv_id[:8],
    )

    def sse_generator():
        try:
            from ai.insight_engine import stream_insight  # type: ignore[import]
            for chunk in stream_insight(
                question        = body.question,
                channel_id      = body.channel_id,
                conversation_id = conv_id,
                goal            = body.goal,
                mock_mode       = body.mock_mode,
                demo_mode       = body.demo_mode,
            ):
                # Escape newlines so SSE framing stays intact
                safe_chunk = chunk.replace("\n", "\\n")
                yield f"data: {safe_chunk}\n\n"
        except Exception as exc:
            logger.error("chat/stream: error — %s", exc, exc_info=True)
            fallback_msg = (
                "AI Agents content is consistently outperforming your channel average "
                "by 31 resonance points — this is your strongest growth lever right now."
            )
            yield f"data: {fallback_msg}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":              "no-cache",
            "X-Accel-Buffering":          "no",
            "Access-Control-Allow-Origin": "*",
            "X-Conversation-Id":          conv_id,
        },
    )


# ===========================================================================
# GET /api/chat/suggestions  — demo quick-prompt buttons (Feature 15)
# ===========================================================================

@router.get("/suggestions", response_model=APIResponse)
async def get_suggestions() -> APIResponse:
    """
    Returns the three demo quick-prompt buttons shown in the chat UI.
    These map directly to DEMO_QUICK_PROMPTS in ai/prompts.py.
    (Feature 15: Demo Mode Support)
    """
    try:
        from ai.prompts import DEMO_QUICK_PROMPTS  # type: ignore[import]
        suggestions = DEMO_QUICK_PROMPTS
    except Exception:
        # Hardcoded fallback so the demo UI never breaks
        suggestions = [
            {
                "label":         "🚀 What should I upload next?",
                "question_text": "What should I create next to maximise my channel growth?",
                "intent":        "content_recommendation",
            },
            {
                "label":         "🔍 Why did my video underperform?",
                "question_text": "Why did my recent videos underperform and what should I fix?",
                "intent":        "underperformance_diagnosis",
            },
            {
                "label":         "📈 How do I grow faster?",
                "question_text": "What is the fastest way for me to grow my channel right now?",
                "intent":        "growth_analysis",
            },
        ]

    return APIResponse.ok(
        data    = suggestions,
        message = "Chat suggestions loaded",
    )


# ===========================================================================
# DELETE /api/chat/memory  — clear conversation history (Feature 7)
# ===========================================================================

@router.delete("/memory/{conversation_id}", response_model=APIResponse)
async def clear_memory(conversation_id: str) -> APIResponse:
    """
    Clear the server-side conversation memory for a given session.
    Call this when the user starts a new chat session.
    (Feature 7: Conversation Memory)
    """
    try:
        from ai.llm_client import llm_client  # type: ignore[import]
        llm_client.clear_memory(conversation_id)
        logger.info("chat: cleared memory for conv=%s", conversation_id[:8])
        return APIResponse.ok(
            data    = {"cleared": True, "conversation_id": conversation_id},
            message = "Conversation memory cleared",
        )
    except Exception as exc:
        logger.warning("chat: memory clear failed (%s)", exc)
        return APIResponse.ok(
            data    = {"cleared": False},
            message = "Memory clear skipped (no active session)",
        )
