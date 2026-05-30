"""
ai/llm_client.py
CreatorPulse · Claude Communication Engine

Role: The bridge between the CreatorPulse analytics system and Claude.
      Handles all LLM interactions: prompt execution, streaming, retry logic,
      token management, conversation memory, fallback responses, and
      structured output normalisation.

Used by:
  ai/insight_engine.py  — primary caller for insight generation
  routes/chat.py        — streaming SSE endpoint
  routes/insights.py    — batch insight generation
"""

from __future__ import annotations

from config.settings import settings

import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import Anthropic SDK — fall back to mock mode if unavailable.
# (Feature 18: Mock Mode Compatibility)
# ---------------------------------------------------------------------------
try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False
    logger.warning("llm_client: anthropic SDK not found — mock mode active")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Change line ~43:
DEFAULT_MODEL: str = settings.llm_model  # was "claude-opus-4-5"
FALLBACK_MODEL: str = "claude-haiku-4-5"
MAX_TOKENS:          int   = 1500
CONTEXT_MAX_TOKENS:  int   = 3000    # max chars for analytics context block
TIMEOUT_SECONDS:     int   = 20
MAX_RETRIES:         int   = 3
RETRY_BACKOFF_BASE:  float = 1.5     # seconds; multiplied by attempt number
CONVERSATION_WINDOW: int   = 6       # max turns kept in memory


# ===========================================================================
# Data structures
# ===========================================================================

@dataclass
class LLMRequest:
    """
    Fully-assembled request to send to the LLM.
    Built by build_request(); passed to ask() or stream().
    (Feature 5: Structured Prompt Builder)
    """
    system_prompt:     str
    user_prompt:       str
    model:             str             = DEFAULT_MODEL
    max_tokens:        int             = MAX_TOKENS
    stream:            bool            = False
    conversation_id:   str | None      = None   # for memory (Feature 13)
    metadata:          dict[str, Any]  = field(default_factory=dict)


@dataclass
class LLMResponse:
    """
    Normalised response from the LLM.
    (Feature 8: Response Formatting)
    """
    content:        str
    model_used:     str        = DEFAULT_MODEL
    input_tokens:   int        = 0
    output_tokens:  int        = 0
    latency_ms:     int        = 0
    from_cache:     bool       = False
    from_mock:      bool       = False
    confidence:     float      = 0.0    # Feature 15

    # Parsed structured fields (populated by parse_structured_response)
    summary:         str              = ""
    key_insight:     str              = ""
    signals:         list[str]        = field(default_factory=list)
    recommendation:  str              = ""
    sources_used:    list[str]        = field(default_factory=list)


@dataclass
class ConversationTurn:
    """Single turn stored in conversation memory. (Feature 13)"""
    role:    str    # "user" | "assistant"
    content: str


# ===========================================================================
# Conversation memory (Feature 13)
# ===========================================================================

class ConversationMemory:
    """
    In-memory sliding-window conversation store.
    Keyed by conversation_id; each value is a deque of ConversationTurn.
    """

    def __init__(self, window: int = CONVERSATION_WINDOW) -> None:
        self._window = window
        self._store: dict[str, deque[ConversationTurn]] = {}

    def add(self, conversation_id: str, role: str, content: str) -> None:
        if conversation_id not in self._store:
            self._store[conversation_id] = deque(maxlen=self._window)
        self._store[conversation_id].append(ConversationTurn(role=role, content=content))

    def get_history(self, conversation_id: str) -> list[dict[str, str]]:
        """Return turns as Anthropic-compatible message dicts."""
        turns = self._store.get(conversation_id, deque())
        return [{"role": t.role, "content": t.content} for t in turns]

    def clear(self, conversation_id: str) -> None:
        self._store.pop(conversation_id, None)


_memory = ConversationMemory()


# ===========================================================================
# Response cache (simple in-memory, Feature 10 / performance)
# ===========================================================================

class _ResponseCache:
    """
    TTL-based in-memory cache keyed on a hash of (system_prompt, user_prompt).
    Prevents identical demo questions from burning API quota.
    """
    TTL: int = 300   # 5 minutes

    def __init__(self) -> None:
        self._store: dict[str, tuple[LLMResponse, float]] = {}

    def _key(self, req: LLMRequest) -> str:
        raw = req.system_prompt[:200] + req.user_prompt[:400]
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, req: LLMRequest) -> LLMResponse | None:
        key = self._key(req)
        entry = self._store.get(key)
        if entry and (time.time() - entry[1]) < self.TTL:
            resp = entry[0]
            resp.from_cache = True
            return resp
        return None

    def set(self, req: LLMRequest, resp: LLMResponse) -> None:
        self._store[self._key(req)] = (resp, time.time())


_cache = _ResponseCache()


# ===========================================================================
# Token management helpers (Feature 12)
# ===========================================================================

def _truncate_context(context: str, max_chars: int = CONTEXT_MAX_TOKENS) -> str:
    """
    Truncate a context block to `max_chars` characters, keeping the most
    relevant lines (top of the block) and appending a note when truncated.
    (Feature 12: Token Management)
    """
    if len(context) <= max_chars:
        return context
    truncated = context[:max_chars]
    # Snap to last newline to avoid cutting mid-sentence
    cut = truncated.rfind("\n")
    if cut > max_chars * 0.8:
        truncated = truncated[:cut]
    return truncated + "\n\n[... context truncated for token efficiency ...]"


# ===========================================================================
# Mock responses (Features 17, 18)
# ===========================================================================

_MOCK_RESPONSES: dict[str, str] = {

    # ------------------------------------------------------------------
    # Intent: content_recommendation
    # Trigger: "what should I upload", "what to create", "next video"
    # ------------------------------------------------------------------
    "content_recommendation": (
        '{"summary": "AI Agents content is your highest-leverage upload — 31 resonance points above your channel average.", '
        '"key_insight": "Coral joined YouTube retention data, Discord message spikes, and Sheets CTA clicks '
        'to find your clearest signal: AI Agent tutorials average 84 resonance, 68% retention, and a 3.2× '
        'Discord spike ratio. Your LangGraph Deep Dive hit 91 resonance — the highest single-video score '
        'on your channel. Career Advice, by contrast, averages 53 resonance and generates almost no community '
        'discussion. The data points to one clear action: double down on AI Agents for the next 3 weeks.", '
        '"signals": ['
        '"AI Agents: avg resonance 84/100 | avg retention 68% | Discord spike 3.2×", '
        '"LangGraph Deep Dive: resonance 91/100 — channel best", '
        '"Career Advice: avg resonance 53/100 | retention 41% | 0.8× Discord baseline", '
        '"Google Sheets: AI Agent videos drive 4.1× more CTA clicks than career content"'
        '], '
        '"recommendation": "Upload an AI Agents tutorial this week. Post a teaser in your Discord server '
        '24 hours before publishing — your community pre-seeding pattern adds ~18 Discord messages per video '
        'and pushes resonance above the 80 threshold reliably.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),

    # ------------------------------------------------------------------
    # Intent: underperformance_diagnosis
    # Trigger: "why did it fail", "underperform", "low views", "flop"
    # ------------------------------------------------------------------
    "underperformance_diagnosis": (
        '{"summary": "Two videos show false popularity — high click-through masked by rapid audience drop-off and community silence.", '
        '"key_insight": "Career Q&A #12 pulled 180k impressions and looked strong on the surface, but '
        'Coral cross-referenced YouTube retention (22%), Discord activity (3 messages vs a 62-message baseline), '
        'and Sheets engagement (0 CTA clicks). The diagnosis: title-content mismatch. Viewers clicked expecting '
        'fast actionable answers but the intro took 2+ minutes to deliver value. Productivity Tips #8 shows a '
        'similar pattern — 95k views, 31% retention, engagement ratio 0.008 (below your 0.021 channel average). '
        'Both videos needed a stronger hook in the first 45 seconds.", '
        '"signals": ['
        '"Career Q&A #12: 180k views | 22% retention | 3 Discord msgs | diagnosis: ctr_retention_mismatch", '
        '"Productivity Tips #8: 95k views | 31% retention | engagement ratio 0.008 vs 0.021 avg", '
        '"Discord baseline: 62 msgs/video channel avg — both flagged videos have <5% of that", '
        '"Sheets: zero CTA clicks on both videos — audience did not stay long enough to act"'
        '], '
        '"recommendation": "Restructure the intro: state the core value in the first 30 seconds, not after the intro sequence. '
        'Test two hook variants on your next upload — one direct answer, one curiosity-based — and compare '
        'the 48-hour retention curves.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),

    # ------------------------------------------------------------------
    # Intent: audience_health
    # Trigger: "audience", "community", "engagement", "loyal", "fans"
    # ------------------------------------------------------------------
    "audience_health": (
        '{"summary": "Your audience is active and growing but showing early signs of passive viewing in non-AI content.", '
        '"key_insight": "Coral aggregated Discord message counts, YouTube engagement ratios, and Sheets poll responses '
        'to score your audience health at 74/100 — above average. Your AI Agents viewers are genuinely engaged: '
        '62 Discord messages per video on average, 68% retention, and a positive sentiment ratio of 0.73. '
        'However, your career and productivity content attracts a different, more passive audience segment — '
        '22–31% retention, near-zero Discord activity. This passive segment inflates your subscriber count '
        'but suppresses your overall resonance average. The fix is not to stop career content — '
        'it is to re-angle it toward AI career paths to attract the same engaged audience.", '
        '"signals": ['
        '"Audience health score: 74/100 (above channel avg)", '
        '"AI content viewers: 68% retention | 62 Discord msgs/video | sentiment 0.73 positive", '
        '"Career/productivity viewers: 26% avg retention | <5 Discord msgs/video | passive segment flag", '
        '"Discord community growth: +12% message volume over last 30 days"'
        '], '
        '"recommendation": "Introduce one interactive element per video — a Discord poll, a pinned comment question, '
        'or a community tab post 48 hours after upload. This converts passive viewers into discussers and '
        'raises your community spike ratio.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),

    # ------------------------------------------------------------------
    # Intent: growth_forecast
    # Trigger: "forecast", "predict", "next month", "trajectory"
    # ------------------------------------------------------------------
    "growth_forecast": (
        '{"summary": "Your channel is in an accelerating growth phase — momentum driven entirely by AI Agents content.", '
        '"key_insight": "Coral\'s trend query joined YouTube publish dates with rolling resonance scores and '
        'Discord volume over 90 days. The result: AI Agents videos published in the last 30 days average '
        '84 resonance vs 71 for older videos — a +13 point improvement in a single month. '
        'Projected 7-day growth sits at +8.2% on current trajectory. The risk: Career Advice resonance '
        'has dropped 6.2 points this period, dragging your channel average down. If you maintain 1 AI Agent '
        'upload per week for the next 4 weeks, your channel average is projected to cross 78 resonance — '
        'the threshold where algorithmic recommendation typically accelerates.", '
        '"signals": ['
        '"7-day growth forecast: +8.2% on current trajectory", '
        '"AI Agents resonance trend: +13 pts over 30 days (accelerating)", '
        '"Career Advice resonance trend: -6.2 pts (declining — de-prioritise)", '
        '"Projection: channel avg resonance hits 78 in 4 weeks if AI Agent cadence holds"'
        '], '
        '"recommendation": "Publish AI Agent content every Tuesday for 4 consecutive weeks. '
        'Use your Discord to tease each video 24 hours early — the pre-seed pattern is already adding '
        'measurable resonance lift to your best-performing videos.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),

    # ------------------------------------------------------------------
    # Intent: growth_analysis
    # Trigger: "grow", "growth", "faster", "bigger channel", "subscribers"
    # ------------------------------------------------------------------
    "growth_analysis": (
        '{"summary": "Your single biggest growth lever is AI Agents content — it outperforms every other topic by every metric Coral can measure.", '
        '"key_insight": "CreatorPulse ran a cross-source JOIN across YouTube analytics, Discord community signals, '
        'and Google Sheets engagement data. The result is unambiguous: AI Agents content generates 3.2× more '
        'Discord discussion, 68% average retention vs your 45% channel average, and 4.1× more CTA clicks. '
        'Your LangGraph tutorial alone generated a 4.1× community spike and 91 resonance — both well above '
        'the viral threshold of 3.0× spike and 80 resonance. The compounding effect is significant: '
        'higher Discord activity signals stronger community to the YouTube algorithm, which increases '
        'impression share, which drives more views on future uploads in the same topic.", '
        '"signals": ['
        '"AI Agents: 84 avg resonance | 3.2× Discord spike | 68% retention | 4.1× CTA clicks", '
        '"Channel avg: 56 resonance | 1.0× Discord baseline | 45% retention", '
        '"LangGraph viral event: 91 resonance | 4.1× spike | action window 72h", '
        '"Compounding signal: AI Agents topic rank #1 across all 3 sources simultaneously"'
        '], '
        '"recommendation": "Your fastest path to growth is 2 AI Agent tutorials in the next 3 weeks, '
        'each promoted in Discord 24h before publishing. Based on current trajectory, this will push '
        'your channel average above 78 resonance — the threshold for algorithmic acceleration.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),

    # ------------------------------------------------------------------
    # Intent: resonance_explanation
    # Trigger: "resonance", "score", "what is resonance", "how is score calculated"
    # ------------------------------------------------------------------
    "resonance_explanation": (
        '{"summary": "Resonance Score is CreatorPulse\'s cross-source metric — a weighted signal from YouTube retention, Discord community activity, engagement quality, and audience sentiment.", '
        '"key_insight": "Coral joins three data sources in a single SQL query to compute Resonance Score: '
        'YouTube watch percentage (40% weight) — how long people actually watch; '
        'Discord message count normalised to a 50-message baseline (30% weight) — whether your community cares enough to discuss; '
        'engagement ratio of likes + comments to views (20% weight) — action quality; '
        'and positive Discord sentiment ratio (10% weight) — community mood. '
        'A score above 80 means your video is performing exceptionally across all three platforms simultaneously. '
        'Your LangGraph tutorial scores 91 because it hits 66% retention, 4.1× Discord spike, 0.038 engagement ratio, '
        'and 0.81 positive sentiment — all above threshold. Career Q&A #12 scores 24 despite 180k views '
        'because retention collapsed at 22% and community was silent.", '
        '"signals": ['
        '"Resonance formula: watch_pct×0.40 + discord_norm×0.30 + engagement×0.20 + sentiment×0.10", '
        '"LangGraph: 91 score — 66% retention | 4.1× Discord | 0.038 engagement | 0.81 sentiment", '
        '"Career Q&A #12: 24 score — 22% retention | 0.05× Discord | 0.006 engagement", '
        '"Channel avg: 56 resonance — room to grow by focusing on retention and community pre-seeding"'
        '], '
        '"recommendation": "Focus your optimisation on the two biggest resonance levers: '
        'retention (first 45 seconds) and Discord pre-seeding. Improving both by 10% '
        'on your next upload will push resonance above 70 even for career content.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),

    # ------------------------------------------------------------------
    # Intent: demo
    # Trigger: demo mode quick-start
    # ------------------------------------------------------------------
    "demo": (
        '{"summary": "CreatorPulse is live — Coral has joined YouTube, Discord, and Google Sheets into one intelligence layer.", '
        '"key_insight": "This is a live demo of CreatorPulse powered by Coral SQL. '
        'Right now, Coral has registered three local data sources — youtube.videos, discord.messages, '
        'and gsheets.engagement_log — and is running cross-source JOIN queries to power every insight '
        'you see here. No ETL, no warehouse, no glue code. One SQL query. Three platforms. '
        'Your top finding: AI Agents content outperforms your channel average by 31 resonance points, '
        'driven by a 3.2× Discord spike and 68% average watch retention. '
        'Ask me anything about your channel — what to upload next, why a video underperformed, '
        'or how your audience is trending.", '
        '"signals": ['
        '"Coral sources active: youtube.videos | discord.messages | gsheets.engagement_log", '
        '"Cross-source JOIN: 3 platforms, 0 glue code, 1 SQL query", '
        '"Top signal: AI Agents resonance 84/100 — 31 pts above channel avg", '
        '"Community: 62 avg Discord msgs/video | sentiment 0.73 positive"'
        '], '
        '"recommendation": "Start with: \'What should I upload next?\' or \'Why did my last video underperform?\' '
        'to see CreatorPulse analyse your real channel data across all three sources.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),

    # ------------------------------------------------------------------
    # Intent: general_chat / default
    # Trigger: anything else
    # ------------------------------------------------------------------
    "general_chat": (
        '{"summary": "Your channel is in a strong position — AI Agents content is pulling ahead and your community health is above average.", '
        '"key_insight": "Coral cross-referenced YouTube performance, Discord community signals, and Google Sheets '
        'engagement data to give you a full-picture view of your channel. '
        'Channel average resonance sits at 56/100, but your top content category — AI Agents — '
        'is averaging 84 resonance with 68% retention and a 3.2× Discord spike ratio. '
        'Your community is active: 62 Discord messages per video on average, trending upward. '
        'Two videos are flagged as underperformers (Career Q&A #12 and Productivity Tips #8) '
        'due to low retention and community silence. '
        'The single clearest action: prioritise AI Agents content for the next 4 weeks '
        'and restructure the hooks on career content.", '
        '"signals": ['
        '"Channel resonance avg: 56/100 (AI Agents: 84 | Career: 53)", '
        '"Best video: LangGraph Deep Dive — 91 resonance | 66% retention | 4.1× Discord spike", '
        '"Community health: 74/100 | 62 Discord msgs/video avg | +12% volume 30-day trend", '
        '"2 underperformers flagged: Career Q&A #12 (22% retention) | Productivity Tips #8 (31% retention)"'
        '], '
        '"recommendation": "Your top priority: publish 1 AI Agents tutorial this week and promote it in Discord 24h before upload. '
        'Your community pre-seeding pattern consistently lifts resonance above 80 when done right.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),

    "default": (
        '{"summary": "Your channel is in a strong position — AI Agents content is pulling ahead and your community health is above average.", '
        '"key_insight": "Coral cross-referenced YouTube performance, Discord community signals, and Google Sheets '
        'engagement data to give you a full-picture view of your channel. '
        'Channel average resonance sits at 56/100, but your top content category — AI Agents — '
        'is averaging 84 resonance with 68% retention and a 3.2× Discord spike ratio. '
        'Your community is active: 62 Discord messages per video on average, trending upward.", '
        '"signals": ['
        '"Channel resonance avg: 56/100 (AI Agents: 84 | Career: 53)", '
        '"Best video: LangGraph Deep Dive — 91 resonance | 66% retention | 4.1× Discord spike", '
        '"Community health: 74/100 | 62 Discord msgs/video avg"'
        '], '
        '"recommendation": "Publish 1 AI Agents tutorial this week and promote it in Discord 24h before upload.", '
        '"sources_used": ["YouTube", "Discord", "Sheets"]}'
    ),
}


def _get_mock_response(intent: str, latency_ms: int = 120) -> LLMResponse:
    """Return a canned demo response for the given intent. (Feature 18)"""
    content = _MOCK_RESPONSES.get(intent, _MOCK_RESPONSES["default"])
    resp = LLMResponse(
        content       = content,
        model_used    = "claude-sonnet-4-6",
        input_tokens  = 1240,
        output_tokens = len(content.split()),
        latency_ms    = latency_ms,
        from_mock     = True,
        confidence    = 0.82,
    )
    _parse_structured_into(resp)
    return resp


# ===========================================================================
# Structured response parser (Feature 8)
# ===========================================================================

def _parse_structured_into(resp: LLMResponse) -> None:
    """
    Attempt to parse the response content as JSON and populate the
    structured fields on the LLMResponse object in-place.
    Falls back gracefully if content is plain text. (Feature 8)
    """
    try:
        # Strip markdown code fences if present
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        data: dict[str, Any] = json.loads(text)
        resp.summary        = str(data.get("summary", ""))
        resp.key_insight    = str(data.get("key_insight", ""))
        resp.signals        = list(data.get("signals", []))
        resp.recommendation = str(data.get("recommendation", ""))
        resp.sources_used   = list(data.get("sources_used", []))
    except (json.JSONDecodeError, KeyError, TypeError):
        # Plain-text response — treat full content as summary
        resp.summary = resp.content[:300]


# ===========================================================================
# Confidence estimation (Feature 15)
# ===========================================================================

def _estimate_confidence(
    resp:           LLMResponse,
    context_length: int,
    data_points:    int,
) -> float:
    """
    Estimate response confidence 0–1 based on:
      - Context richness (more data → higher confidence)
      - Output length (very short = uncertain)
      - Number of data points referenced
    (Feature 15: Confidence Score Generation)
    """
    ctx_score    = min(context_length / CONTEXT_MAX_TOKENS, 1.0) * 0.4
    data_score   = min(data_points / 10, 1.0) * 0.35
    output_score = min(len(resp.content) / 400, 1.0) * 0.25
    return round(ctx_score + data_score + output_score, 2)


# ===========================================================================
# LLM provider base + Anthropic implementation (Feature 4)
# ===========================================================================

class LLMProvider:
    """
    Abstract base — swap for OpenAI / Gemini / local model by subclassing.
    (Feature 4: Multi-Model Support)
    """

    def complete(self, req: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    def stream(self, req: LLMRequest) -> Iterator[str]:
        raise NotImplementedError


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider. (Feature 1: Claude API Integration)"""

    def __init__(self, api_key: str | None = None) -> None:
        if not _ANTHROPIC_AVAILABLE:
            raise RuntimeError("anthropic SDK is not installed")
        import os
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = _anthropic.Anthropic(api_key=key)

    def _build_messages(self, req: LLMRequest) -> list[dict[str, str]]:
        """Build the messages array, prepending conversation history."""
        history = _memory.get_history(req.conversation_id) if req.conversation_id else []
        return history + [{"role": "user", "content": req.user_prompt}]

    def complete(self, req: LLMRequest) -> LLMResponse:
        """Non-streaming completion. (Feature 2: Prompt Execution Engine)"""
        t0 = time.time()
        message = self._client.messages.create(
            model      = req.model,
            max_tokens = req.max_tokens,
            system     = req.system_prompt,
            messages   = self._build_messages(req),
            timeout    = TIMEOUT_SECONDS,
        )
        latency_ms = int((time.time() - t0) * 1000)
        content    = message.content[0].text if message.content else ""

        return LLMResponse(
            content       = content,
            model_used    = message.model,
            input_tokens  = message.usage.input_tokens,
            output_tokens = message.usage.output_tokens,
            latency_ms    = latency_ms,
        )

    def stream(self, req: LLMRequest) -> Iterator[str]:
        """
        Streaming completion — yields text chunks.
        (Feature 7: Streaming Responses Support)
        """
        with self._client.messages.stream(
            model      = req.model,
            max_tokens = req.max_tokens,
            system     = req.system_prompt,
            messages   = self._build_messages(req),
            timeout    = TIMEOUT_SECONDS,
        ) as stream_ctx:
            for text in stream_ctx.text_stream:
                yield text


# ===========================================================================
# LLMClient — public API used by insight_engine.py and routes/
# ===========================================================================

class LLMClient:
    """
    High-level client that wraps a provider with retry logic, caching,
    token management, conversation memory, and fallback responses.

    Instantiate once as a module singleton (see bottom of file).
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        mock_mode: bool = False,
    ) -> None:
        self._mock_mode = mock_mode or not _ANTHROPIC_AVAILABLE
        self._provider: LLMProvider | None = provider

        if not self._mock_mode and provider is None:
            try:
                self._provider = AnthropicProvider()
            except Exception as exc:
                logger.warning("llm_client: could not init Anthropic provider (%s) — mock mode", exc)
                self._mock_mode = True

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def ask(
        self,
        question:        str,
        context:         str = "",
        intent:          str = "default",
        conversation_id: str | None = None,
        structured:      bool = True,
        data_points:     int  = 0,
        demo_mode:       bool = False,
    ) -> LLMResponse:
        """
        Send a question to Claude with analytics context and return a
        normalised LLMResponse.

        Pipeline:
          1. Build request (truncate context if needed)
          2. Check cache
          3. Call provider with retry logic
          4. Update conversation memory
          5. Parse structured output
          6. Estimate confidence
          7. Log and return

        (Features 2, 3, 5, 8, 9, 10, 12, 13, 14, 15, 16, 19)
        """
        from ai.prompts import SYSTEM_PROMPT, build_prompt

        # ── 1. Build request ──────────────────────────────────────────────
        truncated_context = _truncate_context(context)   # Feature 12
        user_prompt       = build_prompt(
            question        = question,
            context_block   = truncated_context,
            intent          = intent,
            structured_output = structured,
            demo_mode       = demo_mode,
        )
        req = LLMRequest(
            system_prompt   = SYSTEM_PROMPT,
            user_prompt     = user_prompt,
            conversation_id = conversation_id,
        )

        # ── 2. Cache check ────────────────────────────────────────────────
        if not conversation_id:    # don't cache contextual conversation turns
            cached = _cache.get(req)
            if cached:
                logger.debug("llm_client: cache hit for intent=%s", intent)
                return cached

        # ── 3 & 4. Call with retry + memory update ─────────────────────────
        resp = self._call_with_retry(req, intent)

        if conversation_id:
            _memory.add(conversation_id, "user",      question)
            _memory.add(conversation_id, "assistant", resp.content)

        # ── 5. Parse structured output ────────────────────────────────────
        if structured:
            _parse_structured_into(resp)

        # ── 6. Confidence ─────────────────────────────────────────────────
        resp.confidence = _estimate_confidence(resp, len(truncated_context), data_points)

        # ── 7. Log ────────────────────────────────────────────────────────
        logger.info(
            "llm_client: intent=%s model=%s tokens_in=%d tokens_out=%d "
            "latency=%dms confidence=%.2f cache=%s mock=%s",
            intent, resp.model_used, resp.input_tokens, resp.output_tokens,
            resp.latency_ms, resp.confidence, resp.from_cache, resp.from_mock,
        )

        if not conversation_id:
            _cache.set(req, resp)

        return resp

    def stream_ask(
        self,
        question:        str,
        context:         str = "",
        intent:          str = "default",
        conversation_id: str | None = None,
        demo_mode:       bool = False,
    ) -> Iterator[str]:
        """
        Stream Claude's response token by token.
        Caller wraps this in an SSE generator for routes/chat.py.
        (Feature 7: Streaming Responses Support)
        """
        from ai.prompts import SYSTEM_PROMPT, build_prompt

        if self._mock_mode:
            mock = _get_mock_response(intent)
            yield from _simulate_stream(mock.content)
            return

        truncated_context = _truncate_context(context)
        user_prompt       = build_prompt(
            question      = question,
            context_block = truncated_context,
            intent        = intent,
            demo_mode     = demo_mode,
        )
        req = LLMRequest(
            system_prompt   = SYSTEM_PROMPT,
            user_prompt     = user_prompt,
            conversation_id = conversation_id,
            stream          = True,
        )

        full_response: list[str] = []
        try:
            assert self._provider is not None
            for chunk in self._provider.stream(req):
                full_response.append(chunk)
                yield chunk
        except Exception as exc:
            logger.error("llm_client: stream error — %s", exc)
            fallback = self._fallback_response(intent)
            yield from _simulate_stream(fallback.content)
            full_response = [fallback.content]

        if conversation_id:
            joined = "".join(full_response)
            _memory.add(conversation_id, "user",      question)
            _memory.add(conversation_id, "assistant", joined)

    def clear_memory(self, conversation_id: str) -> None:
        """Clear conversation history for a session. (Feature 13)"""
        _memory.clear(conversation_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_with_retry(self, req: LLMRequest, intent: str) -> LLMResponse:
        """
        Execute the provider call with exponential-backoff retry.
        Falls back to mock on persistent failure.
        (Features 10, 11, 17)
        """
        if self._mock_mode:
            return _get_mock_response(intent)

        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                assert self._provider is not None
                return self._provider.complete(req)
            except Exception as exc:
                last_exc = exc
                wait = RETRY_BACKOFF_BASE * attempt
                logger.warning(
                    "llm_client: attempt %d/%d failed (%s) — retrying in %.1fs",
                    attempt, MAX_RETRIES, exc, wait,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(wait)

        logger.error("llm_client: all retries exhausted — falling back: %s", last_exc)
        return self._fallback_response(intent)

    def _fallback_response(self, intent: str) -> LLMResponse:
        """
        Return a pre-baked fallback when the API is completely unavailable.
        (Feature 17: Fallback Response System)
        """
        logger.warning("llm_client: serving fallback response for intent=%s", intent)
        resp = _get_mock_response(intent, latency_ms=0)
        resp.from_mock = True
        return resp


def _simulate_stream(text: str, chunk_size: int = 8) -> Iterator[str]:
    """Simulate token streaming from a complete string (mock + fallback paths)."""
    words = text.split(" ")
    buf: list[str] = []
    for word in words:
        buf.append(word)
        if len(buf) >= chunk_size:
            yield " ".join(buf) + " "
            buf = []
            time.sleep(0.02)
    if buf:
        yield " ".join(buf)


# ---------------------------------------------------------------------------
# Module-level singleton
# Import everywhere as:  from ai.llm_client import llm_client
# ---------------------------------------------------------------------------

llm_client = LLMClient()