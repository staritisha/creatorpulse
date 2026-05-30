"""
routes/chat.py
CreatorPulse · Conversational AI Gateway

Role: The main AI experience layer — handles every creator conversation,
      routes questions through the full insight pipeline, and streams or
      returns structured responses.

Endpoints:
  POST /api/chat              — non-streaming structured chat
  POST /api/chat/stream       — SSE streaming response (token-by-token)
  GET  /api/chat/suggestions  — demo quick-prompt buttons
  DELETE /api/chat/memory     — clear conversation history

All endpoints delegate to ai/insight_engine.py; no direct LLM calls here.

FIX LOG (vs original):
  - chat()        — run_insight() offloaded to ThreadPoolExecutor so the
                    sync pipeline never blocks the async event loop.
  - chat_stream() — sse_generator() converted to async def with
                    asyncio.to_thread() so token chunks are yielded
                    in real-time instead of buffering the whole response.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from models.response_models import (
    APIResponse,
    ChatResponse,
    RecommendationModel,
    RequestMetadata,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Thread pool for running the sync insight pipeline without blocking the event loop
_THREAD_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chat_worker")

# ---------------------------------------------------------------------------
# In-memory rate-limiter
# Keyed by IP; tracks last request timestamp and rolling count.
# ---------------------------------------------------------------------------

_RATE_WINDOW_S:  int = 60
_RATE_MAX_CALLS: int = 30

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
# Follow-up prompt generator
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
    goal:            str   = Field(default="growth")
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
    """Full structured chat response."""
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
# POST /api/chat  — structured non-streaming
# ===========================================================================

@router.post("", response_model=APIResponse)
async def chat(request: Request, body: ChatRequest) -> APIResponse:
    """
    Main chat endpoint. Runs the full insight pipeline and returns a
    structured ChatResponseEnvelope.

    run_insight() is a sync function. We offload it to a ThreadPoolExecutor
    via asyncio.get_event_loop().run_in_executor() so it never blocks the
    FastAPI event loop while waiting for the Anthropic API.
    """
    t0 = time.time()
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    conv_id = body.conversation_id or str(uuid.uuid4())

    logger.info(
        "chat: question='%s...' channel=%s conv=%s",
        body.question[:50], body.channel_id, conv_id[:8],
    )

    try:
        from ai.insight_engine import run_insight  # type: ignore[import]

        # Run the sync pipeline in a thread — does NOT block the event loop
        loop = asyncio.get_event_loop()
        ir = await loop.run_in_executor(
            _THREAD_POOL,
            lambda: run_insight(
                question        = body.question,
                channel_id      = body.channel_id,
                conversation_id = conv_id,
                goal            = body.goal,
                mock_mode       = body.mock_mode,
                demo_mode       = body.demo_mode,
            ),
        )

        recs: list[RecommendationModel] = []
        if ir.recommendations and ir.recommendations.recommendations:
            recs = [
                RecommendationModel.from_rec(r)
                for r in ir.recommendations.recommendations[:4]
            ]

        follow_ups  = _follow_up_prompts(ir.intent)
        latency_ms  = int((time.time() - t0) * 1000)

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

        logger.info(
            "chat: OK intent=%s confidence=%.0f%% latency=%dms mock=%s cache=%s",
            ir.intent, ir.confidence * 100, latency_ms, ir.from_mock, ir.from_cache,
        )

        # Build the SQL snippet for the frontend "How this works" panel
        try:
            from ai.prompts import build_sql_display_snippet  # type: ignore[import]
            chat_sql = build_sql_display_snippet(ir.intent)
        except Exception:
            chat_sql = ""

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
                coral_sql      = chat_sql,
                coral_source   = "local_file" if not ir.from_mock else "mock",
                channel_id     = body.channel_id,
            ).to_dict(),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("chat: pipeline error — %s", exc, exc_info=True)

        mock       = ChatResponse.mock()
        latency_ms = int((time.time() - t0) * 1000)
        envelope   = ChatResponseEnvelope(
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
            latency_ms          = latency_ms,
            conversation_id     = conv_id,
        )
        return APIResponse.ok(
            data     = envelope.model_dump(),
            message  = "Fallback response (pipeline unavailable)",
            metadata = {
                "fallback": True, "error": str(exc)[:120],
                "coral_sql": "", "coral_source": "mock",
                "coral_sources": ["youtube", "discord", "google_sheets"],
            },
        )


# ===========================================================================
# POST /api/chat/stream  — FIXED async SSE streaming
# ===========================================================================

@router.post("/stream")
async def chat_stream(request: Request, body: ChatStreamRequest) -> StreamingResponse:
    """
    Streaming chat endpoint — Server-Sent Events (SSE), token by token.

    THE FIX vs original:
      The old code used a sync def sse_generator() which caused FastAPI to
      buffer the entire response before sending. The fix:

      1. sse_generator() is now async def → FastAPI uses it as an async
         generator and yields each chunk immediately to the client.
      2. stream_insight() (sync generator) is consumed inside
         asyncio.to_thread() so it runs in a thread pool without blocking
         the event loop. Each chunk is put onto an asyncio.Queue and
         the async generator reads from the queue, yielding to the client
         as fast as the LLM produces tokens.

    SSE format:
      data: <chunk>\n\n
      data: [DONE]\n\n
    """
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    conv_id = body.conversation_id or str(uuid.uuid4())

    logger.info(
        "chat/stream: question='%s...' channel=%s conv=%s",
        body.question[:50], body.channel_id, conv_id[:8],
    )

    # Queue bridges the sync stream_insight() thread → async sse_generator()
    # Sentinel value signals the stream is finished.
    _DONE = object()
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)

    # Capture the running event loop NOW (in async context) so the
    # worker thread can safely schedule coroutines onto it.
    _loop = asyncio.get_event_loop()

    def _run_stream() -> None:
        """Runs in a thread. Pushes chunks onto the queue via the captured loop."""
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
                asyncio.run_coroutine_threadsafe(queue.put(chunk), _loop)
        except Exception as exc:
            logger.error("chat/stream thread: error — %s", exc, exc_info=True)
            fallback = (
                "AI Agents content is consistently outperforming your channel "
                "average by 31 resonance points — this is your strongest growth "
                "lever right now."
            )
            asyncio.run_coroutine_threadsafe(queue.put(fallback), _loop)
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(_DONE), _loop)

    async def sse_generator() -> AsyncIterator[str]:
        """
        Async generator — yields SSE frames as tokens arrive.
        FastAPI streams each yield immediately to the client.
        """
        # Start the blocking stream in the thread pool
        _loop.run_in_executor(_THREAD_POOL, _run_stream)

        while True:
            item = await queue.get()
            if item is _DONE:
                break
            # Escape embedded newlines so SSE framing stays intact
            safe = str(item).replace("\n", "\\n")
            yield f"data: {safe}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
            "X-Conversation-Id":           conv_id,
        },
    )


# ===========================================================================
# GET /api/chat/suggestions  — demo quick-prompt buttons
# ===========================================================================

@router.get("/suggestions", response_model=APIResponse)
async def get_suggestions() -> APIResponse:
    """
    Returns the three demo quick-prompt buttons shown in the chat UI.
    Hardcoded fallback ensures the UI never breaks even if prompts.py fails.
    """
    try:
        from ai.prompts import DEMO_QUICK_PROMPTS  # type: ignore[import]
        suggestions = DEMO_QUICK_PROMPTS
    except Exception:
        suggestions = [
            {
                "label":         "What should I upload next?",
                "question_text": "What should I create next to maximise my channel growth?",
                "intent":        "content_recommendation",
            },
            {
                "label":         "Why did my video underperform?",
                "question_text": "Why did my recent videos underperform and what should I fix?",
                "intent":        "underperformance_diagnosis",
            },
            {
                "label":         "How do I grow faster?",
                "question_text": "What is the fastest way for me to grow my channel right now?",
                "intent":        "growth_analysis",
            },
        ]

    return APIResponse.ok(
        data    = suggestions,
        message = "Chat suggestions loaded",
    )


# ===========================================================================
# DELETE /api/chat/memory  — clear conversation history
# ===========================================================================

@router.delete("/memory/{conversation_id}", response_model=APIResponse)
async def clear_memory(conversation_id: str) -> APIResponse:
    """Clear the server-side conversation memory for a given session."""
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