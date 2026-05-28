"""
config/settings.py — CreatorPulse Central Configuration

The single source of truth for all backend configuration.
Loads, validates, and exposes every setting used across the app.
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from project root (works wherever the process is launched from)
# ---------------------------------------------------------------------------
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


def _require(key: str) -> str:
    """Return the env-var value or raise a clear error at startup."""
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(
            f"[CreatorPulse] Required environment variable '{key}' is missing. "
            "Add it to your .env file before starting the server."
        )
    return value


def _bool(key: str, default: bool = False) -> bool:
    raw = _get(key, str(default))
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(key: str, default: int = 0) -> int:
    raw = _get(key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Settings class
# ---------------------------------------------------------------------------

class Settings:
    """
    All CreatorPulse backend configuration in one importable object.

    Usage anywhere in the backend:
        from config.settings import settings
        settings.anthropic_api_key
        settings.use_mock_data
        settings.cache_ttl
    """

    # ------------------------------------------------------------------
    # 1. Environment mode
    # ------------------------------------------------------------------
    # Values: "development" | "production" | "hackathon"
    environment: str = _get("ENVIRONMENT", "development")

    # ------------------------------------------------------------------
    # 2. Debug mode
    # ------------------------------------------------------------------
    # When True: verbose Coral SQL logs, LLM prompts, timings, API errors
    debug: bool = _bool("DEBUG", False)

    # ------------------------------------------------------------------
    # 3. Mock mode
    # ------------------------------------------------------------------
    # When True: every service falls back to data/mock/* instead of live APIs.
    # Lifesaver when YouTube quota, Discord rate limits, or Coral sources are down.
    use_mock_data: bool = _bool("USE_MOCK_DATA", False)

    # ------------------------------------------------------------------
    # 4. Demo mode
    # ------------------------------------------------------------------
    # When True: pre-loaded questions, safe insights, faster responses,
    # guaranteed fallback — designed for judge-facing demos.
    demo_mode: bool = _bool("DEMO_MODE", False)

    # ------------------------------------------------------------------
    # 5. Claude / Anthropic API configuration
    # ------------------------------------------------------------------
    # Required unless mock/demo mode is active.
    anthropic_api_key: Optional[str] = _get("ANTHROPIC_API_KEY")
    llm_model: str = _get("LLM_MODEL", "claude-opus-4-5")
    llm_timeout: int = _int("LLM_TIMEOUT", 60)          # seconds

    # ------------------------------------------------------------------
    # 6. YouTube API configuration
    # ------------------------------------------------------------------
    youtube_api_key: Optional[str] = _get("YOUTUBE_API_KEY")
    youtube_channel_id: Optional[str] = _get("YOUTUBE_CHANNEL_ID")
    youtube_max_results: int = _int("YOUTUBE_MAX_RESULTS", 50)

    # ------------------------------------------------------------------
    # 7. Discord configuration
    # ------------------------------------------------------------------
    discord_bot_token: Optional[str] = _get("DISCORD_BOT_TOKEN")
    discord_guild_id: Optional[str] = _get("DISCORD_GUILD_ID")
    # Comma-separated list of channel IDs, e.g. "1234,5678"
    discord_channel_ids: list[str] = [
        c.strip()
        for c in _get("DISCORD_CHANNEL_IDS", "").split(",")
        if c.strip()
    ]

    # ------------------------------------------------------------------
    # 8. Google Sheets configuration
    # ------------------------------------------------------------------
    google_sheets_id: Optional[str] = _get("GOOGLE_SHEETS_ID")
    google_service_account_path: str = _get(
        "GOOGLE_SERVICE_ACCOUNT_PATH", "credentials/service_account.json"
    )

    # ------------------------------------------------------------------
    # 9. Coral configuration
    # ------------------------------------------------------------------
    # CORAL_PATH: path or command to the Coral CLI binary
    coral_path: str = _get("CORAL_PATH", "coral")
    coral_timeout: int = _int("CORAL_TIMEOUT", 30)       # seconds per query
    coral_cache_enabled: bool = _bool("CORAL_CACHE_ENABLED", True)

    # ------------------------------------------------------------------
    # 10. Cache configuration
    # ------------------------------------------------------------------
    # TTL in seconds for in-memory query result cache (cachetools.TTLCache)
    cache_ttl: int = _int("CACHE_TTL", 300)              # 5 minutes

    # ------------------------------------------------------------------
    # 11. General API timeout
    # ------------------------------------------------------------------
    api_timeout: int = _int("API_TIMEOUT", 30)           # seconds

    # ------------------------------------------------------------------
    # 12. Frontend URL (CORS)
    # ------------------------------------------------------------------
    frontend_url: str = _get("FRONTEND_URL", "http://localhost:3000")

    # ------------------------------------------------------------------
    # 13. Logging configuration
    # ------------------------------------------------------------------
    # Options: DEBUG | INFO | WARNING | ERROR
    log_level: str = _get("LOG_LEVEL", "DEBUG" if _bool("DEBUG", False) else "INFO")

    # ------------------------------------------------------------------
    # 14. Paths
    # ------------------------------------------------------------------
    mock_data_dir: Path = Path(__file__).resolve().parent.parent / "data" / "mock"
    cache_dir: Path = Path(__file__).resolve().parent.parent / "data" / "cached"

    # ------------------------------------------------------------------
    # 15. Demo pre-loaded questions (used when demo_mode=True)
    # ------------------------------------------------------------------
    demo_questions: list[str] = [
        "What should I make next?",
        "Why did my recent videos underperform?",
        "Which topics resonate most with my community?",
    ]

    # ------------------------------------------------------------------
    # 16. Startup validation
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """
        Call once at server startup (e.g. in main.py lifespan).
        Raises EnvironmentError immediately if critical config is missing,
        so the process fails fast instead of crashing mid-demo.
        """
        errors: list[str] = []

        # Skip strict validation in mock / demo mode — sources aren't needed
        if not self.use_mock_data and not self.demo_mode:
            if not self.anthropic_api_key:
                errors.append("ANTHROPIC_API_KEY is required when not in mock/demo mode.")
            if not self.youtube_api_key:
                errors.append("YOUTUBE_API_KEY is required when not in mock/demo mode.")
            if not self.youtube_channel_id:
                errors.append("YOUTUBE_CHANNEL_ID is required when not in mock/demo mode.")
            if not self.discord_bot_token:
                errors.append("DISCORD_BOT_TOKEN is required when not in mock/demo mode.")
            if not self.google_sheets_id:
                errors.append("GOOGLE_SHEETS_ID is required when not in mock/demo mode.")

        if errors:
            msg = "\n".join(f"  • {e}" for e in errors)
            raise EnvironmentError(
                f"\n[CreatorPulse] Startup validation failed — missing required config:\n{msg}\n"
                "Set the missing values in your .env file, or enable USE_MOCK_DATA=true "
                "to run with mock data.\n"
            )

        self._configure_logging()
        self._ensure_dirs()

        logger = logging.getLogger(__name__)
        logger.info(
            "CreatorPulse config loaded | env=%s debug=%s mock=%s demo=%s model=%s",
            self.environment, self.debug, self.use_mock_data, self.demo_mode, self.llm_model,
        )

    def _configure_logging(self) -> None:
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stdout,
            force=True,
        )

    def _ensure_dirs(self) -> None:
        self.mock_data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_hackathon(self) -> bool:
        """
        Hackathon mode: aggressive caching, fast responses, mock fallback.
        Enable with ENVIRONMENT=hackathon in .env.
        """
        return self.environment == "hackathon"

    @property
    def effective_cache_ttl(self) -> int:
        """Return a shorter TTL in hackathon mode for snappier responses."""
        if self.is_hackathon:
            return min(self.cache_ttl, 60)
        return self.cache_ttl

    @property
    def cors_origins(self) -> list[str]:
        """CORS allow-list. Always includes the configured frontend URL."""
        origins = [self.frontend_url]
        if self.is_development or self.debug:
            origins += [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:8000",
            ]
        return list(dict.fromkeys(origins))  # deduplicate, preserve order

    def __repr__(self) -> str:
        return (
            f"<Settings env={self.environment} debug={self.debug} "
            f"mock={self.use_mock_data} demo={self.demo_mode}>"
        )


# ---------------------------------------------------------------------------
# Singleton — import this everywhere
# ---------------------------------------------------------------------------
settings = Settings()
