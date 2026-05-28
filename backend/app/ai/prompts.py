"""
ai/prompts.py
CreatorPulse · AI Brain Instruction System

Role: All system prompts, user prompt templates, and context injection
      builders that control how Claude reasons about creator data.
      Every string Claude receives originates from or passes through this file.

Used by:
  ai/llm_client.py       — system prompt + context assembly
  ai/insight_engine.py   — intent-routed prompt selection
  routes/chat.py         — demo quick-prompt buttons
  routes/insights.py     — structured insight generation

Versioning: bump PROMPT_VERSION when making substantive changes so
            insight_engine.py can log which version produced a response.
"""

from __future__ import annotations

from string import Template
from typing import Any

# ---------------------------------------------------------------------------
# Prompt versioning (Feature 16)
# ---------------------------------------------------------------------------

PROMPT_VERSION: str = "v1.2"

# ---------------------------------------------------------------------------
# Intent routing keys (Feature 18)
# Used by insight_engine.py to select the right prompt template.
# ---------------------------------------------------------------------------

INTENT_GROWTH          = "growth_analysis"
INTENT_UNDERPERFORMANCE = "underperformance_diagnosis"
INTENT_RECOMMENDATION  = "content_recommendation"
INTENT_RESONANCE       = "resonance_explanation"
INTENT_AUDIENCE_HEALTH = "audience_health"
INTENT_GROWTH_FORECAST = "growth_forecast"
INTENT_GENERAL_CHAT    = "general_chat"
INTENT_DEMO            = "demo"

INTENT_KEYWORDS: dict[str, list[str]] = {
    INTENT_UNDERPERFORMANCE: [
        "flop", "underperform", "fail", "why did", "low views", "bad", "worst",
        "decline", "drop", "fell", "didn't work",
    ],
    INTENT_RECOMMENDATION: [
        "what should i make", "next video", "recommend", "suggest", "what topic",
        "what to create", "what to upload", "content idea",
    ],
    INTENT_AUDIENCE_HEALTH: [
        "audience", "loyal", "community health", "discord", "retention trend",
        "who watches", "repeat viewer",
    ],
    INTENT_GROWTH_FORECAST: [
        "predict", "forecast", "future", "grow", "heading", "next month",
        "momentum", "will my channel",
    ],
    INTENT_RESONANCE: [
        "resonance", "score", "why high", "why low", "resonate", "connect",
    ],
    INTENT_GROWTH: [
        "grow faster", "growth", "improve", "better", "strategy", "accelerate",
    ],
}


def classify_intent(question: str) -> str:
    """
    Simple keyword-based intent router.
    Returns one of the INTENT_* constants. (Feature 18)
    Falls back to INTENT_GENERAL_CHAT.
    """
    q_lower = question.lower()
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(kw in q_lower for kw in keywords):
            return intent
    return INTENT_GENERAL_CHAT


# ===========================================================================
# SYSTEM PROMPT  (Features 1, 2, 11, 13, 14, 15, 19)
# ===========================================================================

SYSTEM_PROMPT = f"""\
You are CreatorPulse — an expert AI Creator Growth Strategist and the most \
data-literate advisor a YouTube creator can have.

## Your identity
You are strategic, direct, and deeply analytical. You speak like a trusted \
growth partner who has studied the creator's data, not like a generic chatbot. \
You are motivating but honest — you do not sugarcoat weak signals.

## Data model — Coral SQL sources  (Feature 11: SQL Reasoning)
You have access to cross-platform creator analytics pulled in real time via \
Coral, which treats external APIs as SQL tables:

  youtube.videos        — video_id, title, topic, tag, published_at, views,
                          likes, comments, ctr, watch_pct
  youtube.channels      — channel_id, name, subscribers, total_views
  discord.messages      — message_id, keyword, author_id, reply_count,
                          daily_count, created_at
  discord.channels      — channel_id, name, guild_id
  sheets.engagement_log — video_id, video_title, date, watch_pct,
                          views, likes, comments

Key JOIN paths:
  youtube.videos ↔ sheets.engagement_log  via video_id (title fallback)
  youtube.videos ↔ discord.messages       via topic / keyword correlation
  discord.messages ↔ sheets.engagement_log via keyword / video_title

## Community Resonance Score
The signature CreatorPulse metric (0–100):
  watch_pct component  (40%) — audience retention quality
  discord component    (30%) — community discussion volume and depth
  engagement component (20%) — (likes + comments) / views ratio
  sentiment component  (10%) — community mood signal from Discord
Higher = stronger audience connection. Not a vanity metric — it reflects \
genuine resonance, not raw reach.

## Communication rules  (Feature 15: Tone, Feature 13: Anti-hallucination)
1. ALWAYS cite specific data points — video titles, numbers, dates, scores.
2. NEVER say "based on the data" or "according to the analytics" — just state facts.
3. NEVER fabricate metrics, video titles, or trends that are not in the context.
4. If data is missing or ambiguous, say so explicitly rather than guessing.
5. Compare across platforms — draw connections between YouTube and Discord signals.
6. End every response with ONE concrete next-action recommendation.
7. Keep responses focused — no padding, no generic creator advice not backed by data.
8. Do NOT recommend spam uploads, clickbait tactics, or engagement-bait strategies.
9. Tone: confident, friendly, strategic. Think "smart friend who has seen the numbers."
10. If asked about topics outside creator analytics, briefly acknowledge and redirect.

## Response format  (Feature 12: Structured Output)
Structure non-trivial responses as:
  **Summary** — 1–2 sentence answer to the question
  **Key Insight** — the single most important data-backed finding
  **Signals** — 2–4 bullet points with specific numbers
  **Recommendation** — one clear next action

For short conversational messages, skip the structure and reply naturally.

## Safety rules  (Feature 19)
- Do not diagnose, give medical, legal, or financial advice.
- Do not comment on creator personal life, mental health, or relationships.
- If asked to fabricate analytics for a demo, generate clearly labelled \
  example numbers rather than presenting them as real.
- Prompt version: {PROMPT_VERSION}
"""


# ===========================================================================
# CONTEXT INJECTION TEMPLATES  (Feature 3)
# These build the "user context block" prepended to every question so Claude
# has the full analytics picture before answering.
# ===========================================================================

def build_analytics_context(
    top_videos: list[dict[str, Any]],
    weak_videos: list[dict[str, Any]],
    audience_health: dict[str, Any] | None = None,
    growth_forecast: dict[str, Any] | None = None,
    topic_scores: dict[str, float] | None = None,
) -> str:
    """
    Render a structured markdown context block injected before the user's
    question. Claude receives this as a human-turn prefix so it sits inside
    the conversation rather than as a second system prompt.
    (Feature 3: Context Injection Templates)
    """
    sections: list[str] = []

    # ── Top resonance videos ──────────────────────────────────────────────
    if top_videos:
        rows = "\n".join(
            f"  {i+1}. {v.get('title','?')} — score {v.get('resonance_score','?')} "
            f"| watch {v.get('watch_pct','?')}% "
            f"| Discord {v.get('discord_msg_count','?')} msgs "
            f"[{v.get('source_attribution','YouTube')}]"
            for i, v in enumerate(top_videos[:5])
        )
        sections.append(f"### Top Resonance Videos (last 30 days)\n{rows}")

    # ── Underperforming videos ────────────────────────────────────────────
    if weak_videos:
        rows = "\n".join(
            f"  {i+1}. {v.get('title','?')} — score {v.get('resonance_score','?')} "
            f"| diagnosis: {v.get('primary_diagnosis','unknown')} "
            f"| watch {v.get('watch_pct','?')}%"
            for i, v in enumerate(weak_videos[:3])
        )
        sections.append(f"### Underperforming Videos\n{rows}")

    # ── Topic resonance map ───────────────────────────────────────────────
    if topic_scores:
        rows = "\n".join(
            f"  • {topic}: {score:.0f} resonance"
            for topic, score in sorted(topic_scores.items(), key=lambda x: -x[1])
        )
        sections.append(f"### Topic Resonance Scores\n{rows}")

    # ── Audience health snapshot ──────────────────────────────────────────
    if audience_health:
        ah = audience_health
        sections.append(
            f"### Audience Health\n"
            f"  Score: {ah.get('health_score','?')}/100 — {ah.get('health_label','?')}\n"
            f"  Loyalty index: {ah.get('loyalty_index','?')}\n"
            f"  Community activity: {ah.get('community_activity','?')}\n"
            f"  Retention health: {ah.get('retention_health','?')}%\n"
            f"  Growth sustainability: {ah.get('growth_sustainability','?')}\n"
            f"  Trend: {ah.get('trend_direction','?')}"
        )

    # ── Growth forecast ───────────────────────────────────────────────────
    if growth_forecast:
        gf = growth_forecast
        sections.append(
            f"### Growth Forecast (7-day)\n"
            f"  Predicted resonance: {gf.get('predicted_resonance_7d','?')}\n"
            f"  Expected change: {gf.get('growth_pct_7d','?')}%\n"
            f"  Momentum: {gf.get('momentum_label','?')}\n"
            f"  Confidence: {gf.get('confidence_score','?')}% ({gf.get('confidence_label','?')})\n"
            f"  Best upload day: {gf.get('best_upload_day','Tuesday')}"
        )

    if not sections:
        return "### Analytics Context\n  No data available for this query."

    return "## CreatorPulse Analytics Context\n\n" + "\n\n".join(sections)


def build_resonance_context(video_row: dict[str, Any]) -> str:
    """
    Compact context block for a single video resonance explanation.
    (Feature 6: Resonance Explanation Prompt)
    """
    return (
        f"## Video Context\n"
        f"  Title: {video_row.get('title','?')}\n"
        f"  Topic: {video_row.get('topic','?')}\n"
        f"  Resonance score: {video_row.get('resonance_score','?')} "
        f"({video_row.get('resonance_tier','?')} tier)\n"
        f"  Watch %: {video_row.get('watch_pct','?')}%\n"
        f"  Views: {video_row.get('views','?'):,} | Likes: {video_row.get('likes','?'):,} "
        f"| Comments: {video_row.get('comments','?'):,}\n"
        f"  Discord messages: {video_row.get('discord_msg_count','?')}\n"
        f"  Community spike: {video_row.get('community_spike_ratio','?')}×\n"
        f"  Score breakdown — watch: {video_row.get('score_watch','?')} pts | "
        f"discord: {video_row.get('score_discord','?')} pts | "
        f"engagement: {video_row.get('score_engagement','?')} pts\n"
        f"  Sources: {video_row.get('source_attribution','YouTube')}"
    )


def build_underperformance_context(diagnosis_rows: list[dict[str, Any]]) -> str:
    """
    Context block for underperformance diagnosis queries.
    (Feature 7: Underperformance Diagnosis Prompt)
    """
    if not diagnosis_rows:
        return "## Underperformance Context\n  No underperforming videos detected in this period."

    parts = ["## Underperformance Diagnosis Data"]
    for i, row in enumerate(diagnosis_rows[:5], 1):
        parts.append(
            f"\n### {i}. {row.get('title','?')}\n"
            f"  Underperformance score: {row.get('underperformance_score','?')}/100\n"
            f"  Primary diagnosis: {row.get('primary_diagnosis','?')}\n"
            f"  Active flags: {row.get('diagnosis_flags_list','none')}\n"
            f"  Watch %: {row.get('watch_pct','?')}% "
            f"(channel avg: {row.get('baseline_watch_pct','?')}%)\n"
            f"  Views vs baseline: {row.get('views_vs_baseline_pct','?')}%\n"
            f"  Discord messages: {row.get('discord_msg_count','?')}\n"
            f"  Sources: {row.get('source_attribution','YouTube')}"
        )
    return "\n".join(parts)


# ===========================================================================
# INTENT-SPECIFIC PROMPT TEMPLATES  (Features 4, 5, 7, 8, 9, 10)
# Each template is a string.Template — call .substitute(context=..., question=...)
# ===========================================================================

# ── Content Recommendation ──────────────────────────────────────────────────
CONTENT_RECOMMENDATION_PROMPT = Template("""\
$context

---
**Creator question:** $question

You are advising on what content to create next.

Use the topic resonance scores and growth forecast above to identify:
1. The topic with the strongest resonance AND positive trend momentum
2. Any emerging topic the creator is underinvesting in relative to its audience response
3. A specific format or angle suggestion based on what the community discusses most

Recommend ONE primary content direction with data-backed reasoning. \
Include the resonance delta and Discord activity difference if available.
""")

# ── Underperformance Diagnosis ──────────────────────────────────────────────
UNDERPERFORMANCE_DIAGNOSIS_PROMPT = Template("""\
$context

---
**Creator question:** $question

Diagnose why these videos underperformed. For each video in the context:
1. Identify the root cause from the diagnosis flags (false_popularity, \
low_retention, community_silence, ctr_retention_mismatch, etc.)
2. Explain what the pattern means in plain language a creator would understand
3. Cite the specific numbers that confirm the diagnosis

Generate a clear, ranked diagnosis. Be direct — do not soften bad findings. \
End with one recovery action the creator can take this week.
""")

# ── Resonance Score Explanation ─────────────────────────────────────────────
RESONANCE_EXPLANATION_PROMPT = Template("""\
$context

---
**Creator question:** $question

Explain this video's resonance score in plain language:
1. What drove the score up (cite the strongest component)
2. What held it back (cite the weakest component)
3. How it compares to the channel average
4. One specific action to improve it for the next similar video

Never just restate the numbers — interpret what they mean for this creator's \
content strategy.
""")

# ── Audience Health Analysis ────────────────────────────────────────────────
AUDIENCE_HEALTH_PROMPT = Template("""\
$context

---
**Creator question:** $question

Analyse the audience health data above:
1. Call out the single strongest health signal and explain why it matters
2. Identify the biggest vulnerability (passive audience, low loyalty, burnout, \
negative sentiment — whichever applies)
3. Connect the health score trend to the content topics — is there a topic that \
drives healthier engagement?

Conclude with one community-building recommendation tied to the data.
""")

# ── Growth Strategy ─────────────────────────────────────────────────────────
GROWTH_STRATEGY_PROMPT = Template("""\
$context

---
**Creator question:** $question

Build a data-backed growth strategy:
1. Identify the highest-leverage opportunity (topic, format, upload frequency)
2. Flag the biggest risk to continued growth
3. Quantify the gap: e.g. "AI tutorials outperform career content by X resonance points"
4. Give the one strategy change with the highest expected impact

Ground every recommendation in the analytics context. \
Do not suggest tactics that are not supported by the data.
""")

# ── Growth Forecast Explanation ─────────────────────────────────────────────
GROWTH_FORECAST_PROMPT = Template("""\
$context

---
**Creator question:** $question

Interpret the 7-day growth forecast:
1. Explain the confidence level — what drives it up or down
2. Explain the momentum label — what signals support it
3. Identify the most important lever to improve the forecast
4. If stagnation risk is flagged, explain the combination of signals causing it

Be forward-looking. This creator wants to know what to DO, not just what will happen.
""")

# ── General Chat ─────────────────────────────────────────────────────────────
GENERAL_CHAT_PROMPT = Template("""\
$context

---
**Creator question:** $question

Answer helpfully using the analytics context above. If the question cannot be \
answered from the provided data, say so clearly and suggest which data source \
would answer it. Do not fabricate metrics.
""")

# ── Demo Mode ────────────────────────────────────────────────────────────────
DEMO_PROMPT = Template("""\
$context

---
**Demo question:** $question

This is a live demo. Generate a compelling, specific, data-rich answer that \
showcases the cross-platform intelligence of CreatorPulse. Use real numbers \
from the context. Highlight the Coral SQL JOIN that produced the insight. \
Make judges say "wow". End with a concrete next action.
""")

# Routing map: intent → template  (Feature 18)
INTENT_PROMPT_MAP: dict[str, Template] = {
    INTENT_RECOMMENDATION:   CONTENT_RECOMMENDATION_PROMPT,
    INTENT_UNDERPERFORMANCE: UNDERPERFORMANCE_DIAGNOSIS_PROMPT,
    INTENT_RESONANCE:        RESONANCE_EXPLANATION_PROMPT,
    INTENT_AUDIENCE_HEALTH:  AUDIENCE_HEALTH_PROMPT,
    INTENT_GROWTH:           GROWTH_STRATEGY_PROMPT,
    INTENT_GROWTH_FORECAST:  GROWTH_FORECAST_PROMPT,
    INTENT_GENERAL_CHAT:     GENERAL_CHAT_PROMPT,
    INTENT_DEMO:             DEMO_PROMPT,
}

# ===========================================================================
# DEMO QUICK-PROMPT BUTTONS  (Feature 4, Feature 17)
# Used by routes/chat.py to power the three quick-prompt UI buttons.
# Each entry is (button_label, question_text, intent).
# ===========================================================================

DEMO_QUICK_PROMPTS: list[tuple[str, str, str]] = [
    (
        "What should I make next?",
        "Based on my recent performance, what content topic should I create next "
        "to maximise audience resonance and community growth?",
        INTENT_RECOMMENDATION,
    ),
    (
        "Why did my recent content underperform?",
        "Which of my recent videos underperformed, and why? "
        "Give me a specific diagnosis for each one.",
        INTENT_UNDERPERFORMANCE,
    ),
    (
        "What builds loyal community?",
        "What content topics and formats build the most loyal community "
        "vs passive viewers? Where should I focus to increase audience health?",
        INTENT_AUDIENCE_HEALTH,
    ),
]


# ===========================================================================
# STRUCTURED OUTPUT SPEC  (Feature 12)
# Appended when the caller needs machine-parseable output from Claude.
# insight_engine.py adds this suffix when building InsightResponse objects.
# ===========================================================================

STRUCTURED_OUTPUT_SUFFIX = """

---
**Output format instructions:**
Return your response as valid JSON with this exact schema:
{
  "summary": "<1-2 sentence answer>",
  "key_insight": "<single most important finding>",
  "signals": ["<bullet 1 with number>", "<bullet 2 with number>", ...],
  "recommendation": "<one concrete next action>",
  "sources_used": ["YouTube", "Discord", "Sheets"]  // only list sources present in context
}
Do not include any text outside the JSON block.
"""


# ===========================================================================
# CORAL SQL DISPLAY SNIPPET BUILDER  (Feature 11, Demo UX)
# Generates a human-readable SQL snippet shown in the "How this works"
# collapsible section in the frontend.
# ===========================================================================

def build_sql_display_snippet(intent: str, timeframe_days: int = 30) -> str:
    """
    Return the relevant Coral SQL query as a display string for the
    collapsible "How this works" panel. (Feature 11)
    """
    base_join = f"""\
SELECT yt.title, yt.views, yt.watch_pct,
       disc.msg_count, gs.watch_pct AS sheet_watch_pct,
       -- Resonance Score components
       ROUND(LEAST(yt.watch_pct / 100.0, 1.0) * 40, 1) AS score_watch,
       ROUND(LEAST(disc.msg_count / 50.0, 1.0) * 40, 1) AS score_discord,
       ROUND(((yt.watch_pct/100) + yt.comments/GREATEST(yt.views,1)) * 10, 1) AS score_engagement
FROM youtube.videos AS yt
LEFT JOIN discord.messages_summary AS disc
  ON yt.topic ILIKE '%' || disc.keyword || '%'
LEFT JOIN sheets.engagement_log AS gs
  ON gs.video_id = yt.video_id
WHERE yt.published_at >= NOW() - INTERVAL '{timeframe_days} DAYS'
ORDER BY (score_watch + score_discord + score_engagement) DESC
LIMIT 10;"""

    if intent == INTENT_UNDERPERFORMANCE:
        return f"""\
-- Underperformer detection: high views, low retention, community silence
SELECT yt.title, yt.views, gs.watch_pct,
       disc.msg_count AS discord_messages,
       CASE WHEN yt.views > 50000 AND gs.watch_pct < 40
            AND disc.msg_count < 5 THEN 'false_popularity' END AS diagnosis
FROM youtube.videos AS yt
LEFT JOIN discord.messages_summary AS disc
  ON yt.topic ILIKE '%' || disc.keyword || '%'
LEFT JOIN sheets.engagement_log AS gs
  ON gs.video_id = yt.video_id
WHERE yt.published_at >= NOW() - INTERVAL '{timeframe_days} DAYS'
  AND (yt.views > 50000 AND gs.watch_pct < 40 OR disc.msg_count < 5)
ORDER BY yt.views DESC;"""

    if intent == INTENT_RECOMMENDATION:
        return f"""\
-- Topic resonance ranking for content recommendation
SELECT yt.topic,
       ROUND(AVG(yt.watch_pct/100.0 * 40 + LEAST(d.msg_count/50.0,1)*40), 1) AS avg_resonance,
       COUNT(*) AS video_count,
       SUM(d.msg_count) AS total_community_msgs
FROM youtube.videos AS yt
LEFT JOIN discord.messages_summary AS d
  ON yt.topic ILIKE '%' || d.keyword || '%'
WHERE yt.published_at >= NOW() - INTERVAL '{timeframe_days} DAYS'
GROUP BY yt.topic
ORDER BY avg_resonance DESC;"""

    return base_join


# ===========================================================================
# PUBLIC BUILDER — single entry point for insight_engine.py  (Feature 4)
# ===========================================================================

def build_prompt(
    question: str,
    context_block: str,
    intent: str | None = None,
    structured_output: bool = False,
    demo_mode: bool = False,
) -> str:
    """
    Assemble the final user-turn prompt string for a given question.

    1. Auto-detect intent if not provided (Feature 18)
    2. Select the matching template (Feature 4)
    3. Substitute context and question
    4. Optionally append structured output instructions (Feature 12)
    5. Demo mode overrides with the demo template (Feature 17)

    Returns the complete prompt string ready to pass to llm_client.ask().
    """
    if demo_mode:
        resolved_intent = INTENT_DEMO
    else:
        resolved_intent = intent or classify_intent(question)

    template = INTENT_PROMPT_MAP.get(resolved_intent, GENERAL_CHAT_PROMPT)
    prompt   = template.substitute(context=context_block, question=question)

    if structured_output:
        prompt += STRUCTURED_OUTPUT_SUFFIX

    return prompt
