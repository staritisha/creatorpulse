"""
ai/recommendations.py
CreatorPulse · AI Decision-Making Engine

Role: Converts analytics + AI intelligence into actionable creator
      recommendations — the answer to "What should the creator actually DO?"

Sits at the top of the intelligence stack:
  detectors.py        → finds problems & opportunities
  resonance_score.py  → scores content
  growth_predictor.py → forecasts growth
  llm_client.py       → talks to Claude
  recommendations.py  → decides what to DO next  ← this file

Used by:
  ai/insight_engine.py   — final step of insight pipeline
  routes/insights.py     — /insights/top, /insights/nextbest endpoints
  routes/chat.py         — appended to every chat response
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (Feature 17: Rule thresholds used across recommendation logic)
# ---------------------------------------------------------------------------

RESONANCE_HIGH_THRESHOLD:    float = 75.0
RESONANCE_GAP_SIGNIFICANT:   float = 20.0   # pts difference = "significantly outperforms"
RETENTION_POOR_THRESHOLD:    float = 35.0
VIRAL_VELOCITY_THRESHOLD:    float = 0.08
CONFIDENCE_DATA_RICH:        float = 0.88
CONFIDENCE_MODERATE:         float = 0.70
CONFIDENCE_LOW:              float = 0.50


# ===========================================================================
# Enums & types
# ===========================================================================

class RecommendationCategory(str, Enum):
    CONTENT_STRATEGY  = "content_strategy"    # what to create
    GROWTH            = "growth"              # how to grow faster
    RETENTION         = "retention"           # improve watch %
    COMMUNITY         = "community"           # Discord & engagement
    TIMING            = "timing"              # upload schedule
    TOPIC             = "topic"               # topic focus / diversification
    RISK_MITIGATION   = "risk_mitigation"     # avoid decline
    VIRAL_OPPORTUNITY = "viral_opportunity"   # amplify breakout

class Priority(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


# ===========================================================================
# Core data structure
# ===========================================================================

@dataclass
class Recommendation:
    """
    A single actionable creator recommendation.
    Designed to map directly to a frontend dashboard card.
    (Feature 17: Dashboard Recommendation Cards)
    """
    # Identity
    category:    RecommendationCategory
    priority:    Priority
    title:       str           # short card headline
    action:      str           # concrete thing to do (imperative verb)
    explanation: str           # WHY backed by data (Feature 13)

    # Supporting data
    supporting_metrics: list[str] = field(default_factory=list)   # bullet numbers
    expected_impact:    str       = ""     # "~+15% resonance over 30 days"
    confidence:         float     = 0.0   # 0–1  (Feature 12)
    confidence_label:   str       = ""    # high | moderate | low

    # Optional enrichment
    topic:           str | None = None    # topic this rec applies to
    related_videos:  list[str]  = field(default_factory=list)

    def confidence_pct(self) -> str:
        return f"{round(self.confidence * 100)}%"


@dataclass
class RecommendationSet:
    """
    Ordered set of recommendations for a creator session.
    (Feature 11: Recommendation Ranking)
    """
    recommendations: list[Recommendation] = field(default_factory=list)
    top_opportunity: str = ""     # single-sentence summary of #1 priority
    primary_risk:    str = ""     # single-sentence risk summary
    goal_focus:      str = "growth"  # growth | engagement | community | retention

    def high_priority(self) -> list[Recommendation]:
        return [r for r in self.recommendations if r.priority == Priority.HIGH]

    def by_category(self, cat: RecommendationCategory) -> list[Recommendation]:
        return [r for r in self.recommendations if r.category == cat]


# ===========================================================================
# Confidence scoring helper (Feature 12)
# ===========================================================================

def _confidence(
    data_points: int,
    signal_count: int,
    trend_consistent: bool,
) -> tuple[float, str]:
    """
    Estimate recommendation confidence from evidence quality.
    Returns (score 0–1, label string).
    """
    dp_score      = min(data_points / 10.0, 1.0) * 0.40
    signal_score  = min(signal_count / 4.0,  1.0) * 0.35
    trend_score   = 0.25 if trend_consistent else 0.10
    score = round(dp_score + signal_score + trend_score, 2)
    if score >= CONFIDENCE_DATA_RICH:
        label = "high"
    elif score >= CONFIDENCE_MODERATE:
        label = "moderate"
    else:
        label = "low"
    return score, label


# ===========================================================================
# Individual recommendation builders (Features 1–8, 14)
# ===========================================================================

def _rec_content_strategy(
    topic:               str,
    topic_resonance:     float,
    channel_avg:         float,
    video_count:         int,
    discord_msgs:        float,
) -> Recommendation:
    """
    Feature 1: Content Recommendation Engine
    Feature 9: Personalized to creator's strongest niche
    """
    gap = round(topic_resonance - channel_avg, 1)
    conf, conf_label = _confidence(video_count, 3, gap > 0)
    return Recommendation(
        category     = RecommendationCategory.CONTENT_STRATEGY,
        priority     = Priority.HIGH if gap >= RESONANCE_GAP_SIGNIFICANT else Priority.MEDIUM,
        title        = f"Double down on {topic}",
        action       = (
            f"Publish 2 '{topic}' videos in the next 3 weeks and promote each "
            f"in Discord before upload to pre-seed community discussion."
        ),
        explanation  = (
            f"'{topic}' averages {topic_resonance:.0f} resonance — "
            f"{abs(gap):.0f} points {'above' if gap >= 0 else 'below'} "
            f"your channel average of {channel_avg:.0f}. "
            f"It also generates ~{discord_msgs:.0f} Discord messages per video, "
            f"indicating genuine audience interest beyond view counts."
        ),
        supporting_metrics = [
            f"Topic resonance: {topic_resonance:.0f}/100",
            f"Channel avg: {channel_avg:.0f}/100 (gap: {gap:+.0f})",
            f"Avg Discord msgs/video: {discord_msgs:.0f}",
        ],
        expected_impact = f"~+{min(round(gap * 0.4), 20)}% resonance gain over 30 days",
        confidence      = conf,
        confidence_label= conf_label,
        topic           = topic,
    )


def _rec_topic_prioritization(
    ranked_topics: list[tuple[str, float]],   # (topic, avg_resonance)
) -> Recommendation:
    """
    Feature 2: Topic Prioritization System
    """
    bullets = [
        f"{i+1}. {t} — {s:.0f} resonance"
        for i, (t, s) in enumerate(ranked_topics[:5])
    ]
    best_topic, best_score = ranked_topics[0] if ranked_topics else ("", 0)
    conf, conf_label = _confidence(len(ranked_topics), 2, True)
    return Recommendation(
        category     = RecommendationCategory.TOPIC,
        priority     = Priority.MEDIUM,
        title        = "Prioritise your strongest content niches",
        action       = (
            f"Allocate at least 60% of upcoming uploads to '{best_topic}' and "
            f"the next 1–2 highest-ranked topics. Reduce low-resonance topics to maintenance frequency."
        ),
        explanation  = (
            f"Your topic resonance ranking shows a clear performance hierarchy. "
            f"'{best_topic}' leads at {best_score:.0f} resonance — "
            f"focusing here maximises both algorithmic distribution and community growth."
        ),
        supporting_metrics = bullets,
        expected_impact    = "Clearer content identity reduces audience confusion and improves retention",
        confidence         = conf,
        confidence_label   = conf_label,
    )


def _rec_underperformer_recovery(
    video_title: str,
    primary_diagnosis: str,
    watch_pct: float,
    discord_msgs: int,
) -> Recommendation:
    """
    Feature 3: Underperforming Content Recovery Recommendations
    """
    # Map diagnosis to specific fix
    fixes: dict[str, tuple[str, str]] = {
        "low_retention":         (
            "Tighten the first 30 seconds",
            "Retention drops sharply in the intro. Test removing the recap and leading with "
            "the most valuable insight in the first 20 seconds.",
        ),
        "community_silence":     (
            "Add a discussion prompt to the video and description",
            "No community discussion formed after upload. End the video with a direct question "
            "and pin it in Discord to seed conversation.",
        ),
        "false_popularity":      (
            "Reframe the title and thumbnail to set accurate expectations",
            "High clicks but low retention signals a title-content mismatch. "
            "The thumbnail promises something the video doesn't deliver quickly enough.",
        ),
        "weak_engagement":       (
            "Increase mid-video engagement triggers",
            "Engagement ratio is well below the channel average. "
            "Add 1–2 mid-video calls to action (comments, polls, timestamps).",
        ),
        "ctr_retention_mismatch":(
            "Deliver the thumbnail promise within the first 60 seconds",
            "Strong click-through but weak retention means viewers arrive expecting "
            "something specific and leave when it doesn't appear quickly.",
        ),
    }
    action_text, explanation_extra = fixes.get(
        primary_diagnosis,
        ("Review pacing and hook", "Multiple weak signals suggest structural content issues.")
    )
    conf, conf_label = _confidence(5, 3, False)
    return Recommendation(
        category     = RecommendationCategory.RETENTION,
        priority     = Priority.HIGH,
        title        = f"Fix: '{video_title[:40]}'",
        action       = action_text,
        explanation  = (
            f"'{video_title}' was flagged as '{primary_diagnosis.replace('_', ' ')}'. "
            f"Watch rate: {watch_pct:.0f}% | Discord messages: {discord_msgs}. "
            + explanation_extra
        ),
        supporting_metrics = [
            f"Watch %: {watch_pct:.0f}% (target: 50%+)",
            f"Discord msgs: {discord_msgs} (target: 20+)",
            f"Diagnosis: {primary_diagnosis.replace('_', ' ')}",
        ],
        expected_impact = "Fixing hook and pacing issues typically recovers 10–20% retention",
        confidence      = conf,
        confidence_label= conf_label,
    )


def _rec_growth_strategy(
    momentum_label: str,
    growth_pct_7d:  float,
    best_topic:     str,
    upload_rec:     str,
) -> Recommendation:
    """
    Feature 4: Growth Strategy Recommendations
    """
    conf, conf_label = _confidence(8, 4, momentum_label == "accelerating")
    if momentum_label == "accelerating":
        action = (
            f"Channel momentum is accelerating. Capitalise now: increase upload frequency "
            f"and concentrate on '{best_topic}' while the algorithmic tailwind is active."
        )
        impact = f"Forecasted {growth_pct_7d:+.1f}% resonance change over 7 days"
    elif momentum_label == "declining":
        action = (
            f"Momentum is declining. Pause lower-performing topics and run a 2-week "
            f"experiment with a fresh '{best_topic}' format to reset distribution."
        )
        impact = "Format refresh can break decline cycles within 2–3 upload cycles"
    else:
        action = (
            f"Growth is stable. {upload_rec} "
            f"Focus upcoming videos on '{best_topic}' to build on existing momentum."
        )
        impact = "Consistency compounds: stable creators see +15% avg resonance over 90 days"

    return Recommendation(
        category     = RecommendationCategory.GROWTH,
        priority     = Priority.HIGH if momentum_label != "stable" else Priority.MEDIUM,
        title        = f"Growth strategy: {momentum_label} momentum",
        action       = action,
        explanation  = (
            f"7-day growth forecast: {growth_pct_7d:+.1f}%. "
            f"Momentum is {momentum_label}. Best topic for growth: '{best_topic}'."
        ),
        supporting_metrics = [
            f"7-day forecast: {growth_pct_7d:+.1f}%",
            f"Momentum: {momentum_label}",
            f"Best topic: {best_topic}",
        ],
        expected_impact = impact,
        confidence      = conf,
        confidence_label= conf_label,
    )


def _rec_audience_health(
    health_label:   str,
    flag_passive:   bool,
    flag_burnout:   bool,
    weak_signal:    str,
) -> Recommendation:
    """
    Feature 5: Audience Health Recommendations
    """
    if flag_burnout:
        action = (
            "Run a shorter, interactive format (live Q&A or community challenge) "
            "this week to re-energise your audience before the next regular upload."
        )
        explanation = (
            "Multiple engagement signals declined this period — a sign of audience fatigue. "
            "A format change resets expectations and can spike Discord activity."
        )
        priority = Priority.HIGH
    elif flag_passive:
        action = (
            "Add a pinned discussion question to Discord for every new upload. "
            "End each video with 'Tell me in the comments: [specific question]' "
            "to convert passive viewers into active participants."
        )
        explanation = (
            "Audience health shows a passive pattern: high views but very low comments "
            "and Discord activity. Passive viewers don't become loyal subscribers."
        )
        priority = Priority.HIGH
    else:
        action = (
            f"Audience health is '{health_label}'. Focus on the weakest pillar: {weak_signal}."
        )
        explanation = (
            f"Overall health is solid but '{weak_signal}' is dragging the score. "
            f"Targeting this specifically will have the highest per-effort impact."
        )
        priority = Priority.MEDIUM

    conf, conf_label = _confidence(6, 3, not flag_burnout)
    return Recommendation(
        category     = RecommendationCategory.COMMUNITY,
        priority     = priority,
        title        = "Improve audience health",
        action       = action,
        explanation  = explanation,
        supporting_metrics = [
            f"Health score: {health_label}",
            f"Passive audience: {'yes' if flag_passive else 'no'}",
            f"Burnout signal: {'yes' if flag_burnout else 'no'}",
        ],
        expected_impact = "Community activation lifts loyalty index by ~10–15 pts over 30 days",
        confidence      = conf,
        confidence_label= conf_label,
    )


def _rec_upload_schedule(
    best_day:       str,
    videos_per_week: float,
    upload_gap_weeks: int,
) -> Recommendation:
    """
    Feature 6: Upload Schedule Optimisation
    """
    if upload_gap_weeks >= 4:
        action = (
            f"Re-establish a consistent upload schedule immediately. "
            f"Commit to 1 video/week every {best_day} for the next 4 weeks. "
            f"Consistency signals to algorithms and audience alike."
        )
        priority = Priority.HIGH
        impact   = "Irregular uploads suppress algorithmic distribution by 30–50%"
    elif videos_per_week < 1.0:
        action = (
            f"Aim for at least 1 video/week. {best_day} uploads show "
            f"the strongest engagement pattern — prioritise that slot."
        )
        priority = Priority.MEDIUM
        impact   = "Weekly consistency grows subscriber notification opens by ~20%"
    else:
        action = (
            f"You're posting consistently. Experiment with {best_day} as "
            f"your primary upload day for 4 weeks and track retention differences."
        )
        priority = Priority.LOW
        impact   = "Timing optimisation typically adds 5–12% to first-48h views"

    conf, conf_label = _confidence(4, 2, upload_gap_weeks == 0)
    return Recommendation(
        category     = RecommendationCategory.TIMING,
        priority     = priority,
        title        = f"Optimise upload timing (best day: {best_day})",
        action       = action,
        explanation  = (
            f"Best engagement day: {best_day}. "
            f"Current upload frequency: {videos_per_week:.1f}/week. "
            f"Upload gaps detected: {upload_gap_weeks} periods."
        ),
        supporting_metrics = [
            f"Best upload day: {best_day}",
            f"Current frequency: {videos_per_week:.1f} videos/week",
            f"Gap periods: {upload_gap_weeks}",
        ],
        expected_impact = impact,
        confidence      = conf,
        confidence_label= conf_label,
    )


def _rec_topic_diversification(
    dominant_topic: str,
    declining_topic: str,
    related_topics: list[str],
) -> Recommendation:
    """
    Feature 7: Topic Diversification Suggestions
    """
    suggestions = related_topics[:3] or ["a sub-topic of your current niche"]
    conf, conf_label = _confidence(4, 2, False)
    return Recommendation(
        category     = RecommendationCategory.TOPIC,
        priority     = Priority.MEDIUM,
        title        = f"Diversify away from declining '{declining_topic}'",
        action       = (
            f"Rather than abandoning '{declining_topic}' entirely, angle it toward "
            f"your dominant '{dominant_topic}' niche. Try topics like: "
            f"{', '.join(suggestions)}."
        ),
        explanation  = (
            f"'{declining_topic}' is losing resonance. Pivoting to adjacent topics "
            f"within your proven '{dominant_topic}' niche prevents audience fatigue "
            f"while keeping search discoverability."
        ),
        supporting_metrics = [
            f"Dominant niche: {dominant_topic}",
            f"Declining topic: {declining_topic}",
            f"Suggested pivots: {', '.join(suggestions)}",
        ],
        expected_impact = "Pivot topics within dominant niche retain 70–80% of existing audience",
        confidence      = conf,
        confidence_label= conf_label,
    )


def _rec_viral_opportunity(
    video_title:  str,
    spike_ratio:  float,
    resonance:    float,
) -> Recommendation:
    """
    Feature 8: Viral Opportunity Recommendations
    """
    conf, conf_label = _confidence(3, 3, True)
    return Recommendation(
        category     = RecommendationCategory.VIRAL_OPPORTUNITY,
        priority     = Priority.HIGH,
        title        = "🔥 Breakout opportunity detected",
        action       = (
            f"Create a follow-up to '{video_title[:40]}' immediately while the "
            f"community spike is active. Announce it in Discord today. "
            f"Consider a shorts clip of the most-discussed moment to extend reach."
        ),
        explanation  = (
            f"'{video_title}' triggered a {spike_ratio:.1f}× Discord activity spike "
            f"with {resonance:.0f} resonance — both above breakout thresholds. "
            f"Follow-up content within 72 hours of a spike captures peak algorithmic momentum."
        ),
        supporting_metrics = [
            f"Resonance: {resonance:.0f}/100",
            f"Community spike: {spike_ratio:.1f}× baseline",
            "Action window: 72 hours post-upload",
        ],
        expected_impact = "Follow-up content during spike windows averages 2.4× normal retention",
        confidence      = conf,
        confidence_label= conf_label,
    )


def _rec_risk_mitigation(
    risk_label:  str,
    risk_detail: str,
) -> Recommendation:
    """
    Feature 14: Risk Mitigation Suggestions
    """
    conf, conf_label = _confidence(5, 2, False)
    return Recommendation(
        category     = RecommendationCategory.RISK_MITIGATION,
        priority     = Priority.MEDIUM,
        title        = f"Risk: {risk_label.replace('_', ' ').title()}",
        action       = risk_detail,
        explanation  = (
            f"Early warning signal: {risk_label.replace('_', ' ')}. "
            f"Acting now prevents the signal from compounding into a growth plateau."
        ),
        supporting_metrics = [f"Risk: {risk_label}"],
        expected_impact = "Early intervention prevents 30–50% of growth slowdowns",
        confidence      = conf,
        confidence_label= conf_label,
    )


# ===========================================================================
# Goal-based filtering (Feature 15)
# ===========================================================================

_GOAL_CATEGORY_ORDER: dict[str, list[RecommendationCategory]] = {
    "growth": [
        RecommendationCategory.GROWTH,
        RecommendationCategory.CONTENT_STRATEGY,
        RecommendationCategory.VIRAL_OPPORTUNITY,
        RecommendationCategory.TIMING,
        RecommendationCategory.TOPIC,
        RecommendationCategory.RETENTION,
        RecommendationCategory.COMMUNITY,
        RecommendationCategory.RISK_MITIGATION,
    ],
    "engagement": [
        RecommendationCategory.COMMUNITY,
        RecommendationCategory.RETENTION,
        RecommendationCategory.CONTENT_STRATEGY,
        RecommendationCategory.VIRAL_OPPORTUNITY,
        RecommendationCategory.GROWTH,
        RecommendationCategory.TOPIC,
        RecommendationCategory.TIMING,
        RecommendationCategory.RISK_MITIGATION,
    ],
    "community": [
        RecommendationCategory.COMMUNITY,
        RecommendationCategory.VIRAL_OPPORTUNITY,
        RecommendationCategory.TOPIC,
        RecommendationCategory.CONTENT_STRATEGY,
        RecommendationCategory.TIMING,
        RecommendationCategory.RETENTION,
        RecommendationCategory.GROWTH,
        RecommendationCategory.RISK_MITIGATION,
    ],
    "retention": [
        RecommendationCategory.RETENTION,
        RecommendationCategory.CONTENT_STRATEGY,
        RecommendationCategory.COMMUNITY,
        RecommendationCategory.TOPIC,
        RecommendationCategory.GROWTH,
        RecommendationCategory.VIRAL_OPPORTUNITY,
        RecommendationCategory.TIMING,
        RecommendationCategory.RISK_MITIGATION,
    ],
}


def _sort_by_goal(recs: list[Recommendation], goal: str) -> list[Recommendation]:
    """Sort recommendations by category priority for the given creator goal."""
    order = _GOAL_CATEGORY_ORDER.get(goal, _GOAL_CATEGORY_ORDER["growth"])
    cat_rank = {cat: i for i, cat in enumerate(order)}
    priority_rank = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
    return sorted(
        recs,
        key=lambda r: (priority_rank[r.priority], cat_rank.get(r.category, 99)),
    )


# ===========================================================================
# Mock recommendations (Feature 19: Mock Mode Compatibility)
# ===========================================================================

MOCK_RECOMMENDATION_SET = RecommendationSet(
    recommendations=[
        Recommendation(
            category    = RecommendationCategory.CONTENT_STRATEGY,
            priority    = Priority.HIGH,
            title       = "Double down on AI Agents",
            action      = (
                "Publish 2 AI Agent tutorials in the next 3 weeks. "
                "Announce each in Discord before upload to pre-seed discussion."
            ),
            explanation = (
                "AI Agents content averages 84 resonance — 31 points above your channel "
                "average. It also drives 3.2× more Discord messages per video than "
                "career content, indicating genuine community interest."
            ),
            supporting_metrics = [
                "AI Agents resonance: 84/100",
                "Channel avg: 53/100 (+31 gap)",
                "Avg Discord msgs/video: 124",
            ],
            expected_impact  = "~+18% resonance gain over 30 days",
            confidence       = 0.91,
            confidence_label = "high",
            topic            = "AI Agents",
        ),
        Recommendation(
            category    = RecommendationCategory.RETENTION,
            priority    = Priority.HIGH,
            title       = "Fix: 'Career Q&A #12'",
            action      = "Deliver the core insight within the first 45 seconds",
            explanation = (
                "'Career Q&A #12' had 180k views but only 22% retention — a title-content "
                "mismatch. Viewers clicked expecting quick answers but found a slow intro."
            ),
            supporting_metrics = [
                "Watch %: 22% (target: 50%+)",
                "Discord msgs: 3 (target: 20+)",
                "Diagnosis: ctr_retention_mismatch",
            ],
            expected_impact  = "Hook fixes typically recover 10–20% retention",
            confidence       = 0.78,
            confidence_label = "moderate",
        ),
        Recommendation(
            category    = RecommendationCategory.VIRAL_OPPORTUNITY,
            priority    = Priority.HIGH,
            title       = "🔥 Breakout opportunity: LangGraph tutorial",
            action      = (
                "Create a follow-up LangGraph video immediately. "
                "Announce in Discord today and post a shorts clip of the most-discussed moment."
            ),
            explanation = (
                "The LangGraph tutorial triggered a 4.1× Discord spike with 91 resonance. "
                "Follow-up content within 72 hours of a spike captures peak momentum."
            ),
            supporting_metrics = [
                "Resonance: 91/100",
                "Discord spike: 4.1× baseline",
                "Action window: 72 hours",
            ],
            expected_impact  = "Follow-ups during spike windows avg 2.4× normal retention",
            confidence       = 0.85,
            confidence_label = "high",
            topic            = "LangGraph",
        ),
    ],
    top_opportunity = "AI Agents content outperforms your channel average by 31 resonance points — this is your highest-leverage growth path.",
    primary_risk    = "Career advice content is declining. Reducing its frequency before audience fatigue sets in will protect overall channel health.",
    goal_focus      = "growth",
)


# ===========================================================================
# Public API
# ===========================================================================

def build_recommendations(
    resonance_rows:  list[dict[str, Any]],
    trend_rows:      list[dict[str, Any]] | None = None,
    health_data:     dict[str, Any] | None       = None,
    forecast_data:   dict[str, Any] | None       = None,
    detector_report: Any | None                  = None,   # DetectionReport
    goal:            str                         = "growth",
    mock_mode:       bool                        = False,
) -> RecommendationSet:
    """
    Build a ranked RecommendationSet from Coral query rows and scoring results.

    Priority order:
      1. Viral opportunities (time-sensitive)
      2. High-resonance content strategy
      3. Underperformer recovery
      4. Audience health fixes
      5. Growth strategy
      6. Upload schedule
      7. Topic diversification
      8. Risk mitigation

    (Features 1–11, 15, 18, 19)
    """
    if mock_mode or not resonance_rows:
        logger.info("recommendations: serving mock recommendation set")
        return MOCK_RECOMMENDATION_SET

    recs: list[Recommendation] = []

    total = len(resonance_rows)
    all_res_scores = [float(r.get("resonance_score", 0)) for r in resonance_rows]
    channel_avg    = sum(all_res_scores) / total if all_res_scores else 0.0

    # ── Topic map ──────────────────────────────────────────────────────────
    topic_buckets: dict[str, list[float]]        = {}
    topic_discord: dict[str, list[float]]        = {}
    for row in resonance_rows:
        t = str(row.get("topic", ""))
        if t:
            topic_buckets.setdefault(t, []).append(float(row.get("resonance_score", 0)))
            topic_discord.setdefault(t, []).append(float(row.get("discord_msg_count", 0)))

    topic_avgs    = {t: sum(v) / len(v) for t, v in topic_buckets.items()}
    topic_disc    = {t: sum(v) / len(v) for t, v in topic_discord.items()}
    ranked_topics = sorted(topic_avgs.items(), key=lambda x: -x[1])

    # ── 1. Viral opportunity ──────────────────────────────────────────────
    viral_rows = [
        r for r in resonance_rows
        if float(r.get("community_spike_ratio", 1.0)) >= 3.0
        and float(r.get("resonance_score", 0)) >= RESONANCE_HIGH_THRESHOLD
    ]
    for row in viral_rows[:1]:
        recs.append(_rec_viral_opportunity(
            video_title = str(row.get("title", "")),
            spike_ratio = float(row.get("community_spike_ratio", 3.0)),
            resonance   = float(row.get("resonance_score", 0)),
        ))

    # ── 2. Content strategy — top topic ──────────────────────────────────
    if ranked_topics:
        best_topic, best_score = ranked_topics[0]
        recs.append(_rec_content_strategy(
            topic            = best_topic,
            topic_resonance  = best_score,
            channel_avg      = channel_avg,
            video_count      = len(topic_buckets.get(best_topic, [])),
            discord_msgs     = topic_disc.get(best_topic, 0),
        ))

    # ── 3. Topic prioritisation ───────────────────────────────────────────
    if len(ranked_topics) >= 2:
        recs.append(_rec_topic_prioritization(ranked_topics))

    # ── 4. Underperformer recovery ────────────────────────────────────────
    weak_rows = sorted(
        [r for r in resonance_rows if float(r.get("resonance_score", 100)) < 45],
        key=lambda r: float(r.get("resonance_score", 100)),
    )
    for row in weak_rows[:2]:
        recs.append(_rec_underperformer_recovery(
            video_title        = str(row.get("title", "")),
            primary_diagnosis  = str(row.get("primary_diagnosis", "low_retention")),
            watch_pct          = float(row.get("watch_pct", 0)),
            discord_msgs       = int(row.get("discord_msg_count", 0)),
        ))

    # ── 5. Audience health ────────────────────────────────────────────────
    if health_data:
        recs.append(_rec_audience_health(
            health_label  = str(health_data.get("health_label", "unknown")),
            flag_passive  = bool(health_data.get("flag_passive_audience")),
            flag_burnout  = bool(health_data.get("flag_burnout")),
            weak_signal   = (health_data.get("weak_signals") or ["engagement"])[0],
        ))

    # ── 6. Growth strategy ────────────────────────────────────────────────
    if forecast_data:
        recs.append(_rec_growth_strategy(
            momentum_label = str(forecast_data.get("momentum_label", "stable")),
            growth_pct_7d  = float(forecast_data.get("growth_pct_7d", 0)),
            best_topic     = str(forecast_data.get("best_topic", ranked_topics[0][0] if ranked_topics else "")),
            upload_rec     = str(forecast_data.get("upload_simulation", {}).get("recommended", "")),
        ))

    # ── 7. Upload schedule ────────────────────────────────────────────────
    if forecast_data:
        recs.append(_rec_upload_schedule(
            best_day         = str(forecast_data.get("best_upload_day", "Tuesday")),
            videos_per_week  = float(forecast_data.get("videos_per_week_avg", 1.0)),
            upload_gap_weeks = int(forecast_data.get("upload_gap_weeks", 0)),
        ))

    # ── 8. Topic diversification ──────────────────────────────────────────
    declining_topic = ""
    if forecast_data and forecast_data.get("declining_topic"):
        declining_topic = str(forecast_data["declining_topic"])
    elif len(ranked_topics) >= 2:
        declining_topic = ranked_topics[-1][0]

    if declining_topic and ranked_topics:
        dominant_topic = ranked_topics[0][0]
        recs.append(_rec_topic_diversification(
            dominant_topic  = dominant_topic,
            declining_topic = declining_topic,
            related_topics  = [],   # enriched by insight_engine.py if available
        ))

    # ── 9. Risk mitigation ────────────────────────────────────────────────
    if detector_report and hasattr(detector_report, "channel_signal"):
        for risk in getattr(detector_report.channel_signal, "risk_signals", [])[:2]:
            recs.append(_rec_risk_mitigation(
                risk_label  = risk.replace(" ", "_"),
                risk_detail = risk,
            ))

    # ── Sort by goal and priority (Features 11, 15) ───────────────────────
    sorted_recs = _sort_by_goal(recs, goal)

    # ── Build summary fields ──────────────────────────────────────────────
    top_opp  = sorted_recs[0].explanation if sorted_recs else ""
    risk_rec = next(
        (r for r in sorted_recs if r.category == RecommendationCategory.RISK_MITIGATION),
        None,
    )
    primary_risk = risk_rec.explanation if risk_rec else ""

    logger.info(
        "recommendations: built %d recommendations (goal=%s top=%s)",
        len(sorted_recs), goal,
        sorted_recs[0].title[:40] if sorted_recs else "none",
    )

    return RecommendationSet(
        recommendations = sorted_recs,
        top_opportunity = top_opp,
        primary_risk    = primary_risk,
        goal_focus      = goal,
    )
