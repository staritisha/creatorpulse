"""
config/constants.py — CreatorPulse Fixed Values & Tuning Panel

All hardcoded thresholds, weights, limits, labels, and reusable constants
live here. Change a number once; every file that imports it updates instantly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# 1. Resonance Score Weights
# ---------------------------------------------------------------------------
# Must sum to 1.0 — adjust here to re-balance scoring without touching logic.

WATCH_PERCENT_WEIGHT: Final[float] = 0.40   # YouTube watch-through percentage
DISCORD_WEIGHT: Final[float] = 0.40         # Discord messages relative to baseline
ENGAGEMENT_WEIGHT: Final[float] = 0.20      # Likes + comments engagement ratio

# Sanity assertion — catches accidental weight drift at import time
assert abs(WATCH_PERCENT_WEIGHT + DISCORD_WEIGHT + ENGAGEMENT_WEIGHT - 1.0) < 1e-9, (
    "Resonance score weights must sum to 1.0"
)

# ---------------------------------------------------------------------------
# 2. Resonance Score Thresholds (0–100 scale)
# ---------------------------------------------------------------------------

RESONANCE_POOR_MAX: Final[int] = 30         # 0–30  → "Poor resonance"
RESONANCE_AVERAGE_MAX: Final[int] = 60      # 31–60 → "Average resonance"
RESONANCE_STRONG_MAX: Final[int] = 80       # 61–80 → "Strong resonance"
RESONANCE_VIRAL_MIN: Final[int] = 81        # 81–100 → "Viral resonance"

RESONANCE_LABELS: Final[dict[str, str]] = {
    "poor":    "Poor resonance",
    "average": "Average resonance",
    "strong":  "Strong resonance",
    "viral":   "Viral resonance",
}


def resonance_label(score: float) -> str:
    """Return the human-readable label for a resonance score."""
    if score <= RESONANCE_POOR_MAX:
        return RESONANCE_LABELS["poor"]
    if score <= RESONANCE_AVERAGE_MAX:
        return RESONANCE_LABELS["average"]
    if score <= RESONANCE_STRONG_MAX:
        return RESONANCE_LABELS["strong"]
    return RESONANCE_LABELS["viral"]


# ---------------------------------------------------------------------------
# 3. Underperformer Detection Thresholds
# ---------------------------------------------------------------------------
# A video is considered underperforming when:
#   views >= HIGH_VIEW_THRESHOLD  AND  watch_pct < MIN_WATCH_PERCENT
#   OR
#   discord_msg_count < LOW_DISCORD_THRESHOLD (community ignored it)

MIN_WATCH_PERCENT: Final[float] = 35.0      # below this = low retention
LOW_DISCORD_THRESHOLD: Final[int] = 5       # fewer messages = community silence
HIGH_VIEW_THRESHOLD: Final[int] = 5_000     # views needed to flag as underperformer

# Drop detector: watch% fell by this many points vs the channel average
WATCH_PERCENT_DROP_THRESHOLD: Final[float] = 20.0

# ---------------------------------------------------------------------------
# 4. Community Spike Thresholds
# ---------------------------------------------------------------------------
# Discord spike = message count > SPIKE_MULTIPLIER × daily baseline

SPIKE_MULTIPLIER: Final[float] = 2.0        # 2× baseline = spike
COMMUNITY_BURST_LIMIT: Final[int] = 3       # 3× baseline = burst (strong signal)
SPIKE_WINDOW_HOURS: Final[int] = 48         # rolling window for spike detection
BASELINE_DAYS: Final[int] = 14              # days used to compute daily baseline

# ---------------------------------------------------------------------------
# 5. Growth Prediction Constants
# ---------------------------------------------------------------------------

PREDICTION_DAYS: Final[int] = 7            # days ahead to predict
MIN_HISTORY_REQUIRED: Final[int] = 5       # minimum data points for prediction
TREND_WINDOW: Final[int] = 10              # last N resonance scores used for fit
CONFIDENCE_BAND_STD: Final[float] = 1.5    # ±std multiplier for confidence interval

# ---------------------------------------------------------------------------
# 6. Cache Settings
# ---------------------------------------------------------------------------

CACHE_TTL: Final[int] = 300                # seconds — 5 minutes default TTL
CACHE_MAX_SIZE: Final[int] = 256           # max entries in TTLCache

# Hackathon-mode tighter TTL (used by settings.effective_cache_ttl)
HACKATHON_CACHE_TTL: Final[int] = 60       # 1 minute for snappier repeated demos

# ---------------------------------------------------------------------------
# 7. Coral Query Limits
# ---------------------------------------------------------------------------

MAX_QUERY_ROWS: Final[int] = 200           # cap rows returned per Coral query
DEFAULT_TIMEFRAME_DAYS: Final[int] = 30    # default look-back window
CORAL_QUERY_TIMEOUT: Final[int] = 30       # seconds before query is abandoned
CORAL_SCHEMA_CACHE_TTL_HOURS: Final[int] = 6  # schema cache invalidation

# ---------------------------------------------------------------------------
# 8. Timeframe Presets
# ---------------------------------------------------------------------------

TIMEFRAME_OPTIONS: Final[list[str]] = ["7d", "30d", "90d"]
DEFAULT_TIMEFRAME: Final[str] = "30d"

TIMEFRAME_DAYS: Final[dict[str, int]] = {
    "7d":  7,
    "30d": 30,
    "90d": 90,
}

# ---------------------------------------------------------------------------
# 9. Demo Questions (shown as instant buttons in the chat frontend)
# ---------------------------------------------------------------------------

DEMO_QUESTIONS: Final[list[str]] = [
    "What should I make next?",
    "Why did my recent videos underperform?",
    "Which topics resonate most with my community?",
    "What content builds the most loyal audience?",
    "Show me my top-performing videos this month.",
]

# ---------------------------------------------------------------------------
# 10. LLM Prompt & Context Limits
# ---------------------------------------------------------------------------

MAX_CONTEXT_ROWS: Final[int] = 30          # max data rows included in Claude prompt
MAX_RESPONSE_TOKENS: Final[int] = 1_024    # Claude max_tokens cap
PROMPT_SUMMARY_LIMIT: Final[int] = 500     # characters for inline data summaries

# Number of pre-loaded recommendations to request from Claude
NUM_RECOMMENDATIONS: Final[int] = 3

# ---------------------------------------------------------------------------
# 11. Retry & Error Constants
# ---------------------------------------------------------------------------

MAX_API_RETRIES: Final[int] = 3            # general API call retry count
RETRY_DELAY: Final[float] = 1.5           # seconds between retries (linear)
CORAL_RETRY_LIMIT: Final[int] = 2          # Coral-specific query retries

# HTTP 529 (overloaded) — Claude-specific back-off
LLM_OVERLOAD_RETRY_DELAY: Final[float] = 5.0

# ---------------------------------------------------------------------------
# 12. Source Health Status Labels
# ---------------------------------------------------------------------------
# Used by routes/sources.py and rendered as coloured dots on the frontend.

class SourceStatus:
    HEALTHY  = "healthy"    # 🟢
    DEGRADED = "degraded"   # 🟡
    OFFLINE  = "offline"    # 🔴
    MOCK     = "mock"       # 🔵 — live source replaced by mock data


SOURCE_HEALTH_STATUS: Final[dict[str, str]] = {
    SourceStatus.HEALTHY:  "🟢 Healthy",
    SourceStatus.DEGRADED: "🟡 Degraded",
    SourceStatus.OFFLINE:  "🔴 Offline",
    SourceStatus.MOCK:     "🔵 Mock",
}

# ---------------------------------------------------------------------------
# 13. Recommendation Priorities
# ---------------------------------------------------------------------------

class Priority:
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


PRIORITY_ORDER: Final[dict[str, int]] = {
    Priority.HIGH:   1,
    Priority.MEDIUM: 2,
    Priority.LOW:    3,
}

RECOMMENDATION_PRIORITIES: Final[list[str]] = [
    Priority.HIGH,
    Priority.MEDIUM,
    Priority.LOW,
]

# ---------------------------------------------------------------------------
# 14. Mock Data Paths
# ---------------------------------------------------------------------------
_DATA_ROOT = Path(__file__).resolve().parent.parent / "data"

MOCK_YOUTUBE_PATH: Final[Path]  = _DATA_ROOT / "mock" / "youtube_mock.json"
MOCK_DISCORD_PATH: Final[Path]  = _DATA_ROOT / "mock" / "discord_mock.json"
MOCK_SHEETS_PATH: Final[Path]   = _DATA_ROOT / "mock" / "sheets_mock.json"
MOCK_INSIGHTS_PATH: Final[Path] = _DATA_ROOT / "mock" / "insights_mock.json"
MOCK_RESONANCE_PATH: Final[Path] = _DATA_ROOT / "mock" / "resonance_mock.json"

CACHE_DIR: Final[Path] = _DATA_ROOT / "cached"

# ---------------------------------------------------------------------------
# 15. Platform Labels & Names
# ---------------------------------------------------------------------------
# Single source of truth for source naming — avoids "discord" vs "Discord" drift.

class Platform:
    YOUTUBE       = "YouTube"
    DISCORD       = "Discord"
    GOOGLE_SHEETS = "Google Sheets"
    CORAL         = "Coral"


# Coral SQL table prefixes matching source registration names
CORAL_TABLE_YOUTUBE: Final[str] = "youtube.videos"
CORAL_TABLE_DISCORD: Final[str] = "discord.messages_summary"
CORAL_TABLE_SHEETS:  Final[str] = "gsheets.engagement_log"

ALL_PLATFORMS: Final[list[str]] = [
    Platform.YOUTUBE,
    Platform.DISCORD,
    Platform.GOOGLE_SHEETS,
]

# ---------------------------------------------------------------------------
# 16. Logging Messages
# ---------------------------------------------------------------------------
# Reusable log strings — grep for these to find specific code paths fast.

class LogMsg:
    # Coral
    CORAL_QUERY_START    = "Starting Coral query..."
    CORAL_QUERY_OK       = "Coral query completed in %.2fs — %d rows returned"
    CORAL_QUERY_TIMEOUT  = "Coral query timed out after %ds — falling back"
    CORAL_FALLBACK_MOCK  = "Coral source unreachable — falling back to mock data"
    CORAL_SCHEMA_CACHED  = "Coral schema loaded from disk cache"
    CORAL_SCHEMA_REFRESH = "Refreshing Coral schema (cache expired)"

    # LLM
    LLM_CALL_START       = "Calling Claude (%s) with %d context rows..."
    LLM_CALL_OK          = "Claude insight generated in %.2fs"
    LLM_CALL_RETRY       = "Claude returned 529 — retrying in %.1fs (attempt %d/%d)"
    LLM_CALL_FAILED      = "Claude call failed after %d retries: %s"

    # Mock / demo
    MOCK_MODE_ACTIVE     = "Mock mode is active — serving data from data/mock/"
    DEMO_MODE_ACTIVE     = "Demo mode is active — using pre-loaded questions"

    # Services
    YOUTUBE_FETCH_START  = "Fetching YouTube videos for channel %s..."
    DISCORD_FETCH_START  = "Fetching Discord messages for guild %s..."
    SHEETS_SYNC_START    = "Syncing engagement log from Google Sheets..."
    SHEETS_WRITE_BACK    = "Writing predictions back to Google Sheets..."

    # Startup
    STARTUP_OK           = "CreatorPulse backend started successfully"
    STARTUP_VALIDATION   = "Running startup config validation..."
