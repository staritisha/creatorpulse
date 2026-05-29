"""
services/sheets_service.py — CreatorPulse Creator Memory System

Reads the creator's engagement log from Google Sheets (watch %, CTR,
manual notes, experiments, goals) and writes prediction scores back.
This is the creator-context layer that personalises every Claude insight.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from config.constants import LogMsg, SourceStatus, MAX_API_RETRIES, RETRY_DELAY
from config.settings import settings
from services.cache_service import cache, CacheNS

logger = logging.getLogger(__name__)

# Google Sheets API base
_SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"

# Default tab names — match what the creator names their sheet
_TAB_ENGAGEMENT  = "Engagement Log"
_TAB_GOALS       = "Goals"
_TAB_EXPERIMENTS = "Experiments"
_TAB_PREDICTIONS = "Predictions"


# ---------------------------------------------------------------------------
# 16. Structured Response Models
# ---------------------------------------------------------------------------

@dataclass
class EngagementRow:
    """One row of the creator's engagement tracking spreadsheet."""
    video_id:         str = ""
    video_title:      str = ""
    published_date:   Optional[str] = None
    watch_pct:        float = 0.0          # 0–100
    ctr:              float = 0.0          # 0–100 click-through rate
    avg_view_duration: float = 0.0         # seconds
    content_category: str = "General"
    manual_notes:     str = ""
    experiment_tag:   str = ""             # e.g. "short-hook", "storytelling"
    resonance_score:  Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id":          self.video_id,
            "video_title":       self.video_title,
            "published_date":    self.published_date,
            "watch_pct":         round(self.watch_pct, 2),
            "ctr":               round(self.ctr, 2),
            "avg_view_duration": round(self.avg_view_duration, 1),
            "content_category":  self.content_category,
            "manual_notes":      self.manual_notes,
            "experiment_tag":    self.experiment_tag,
            "resonance_score":   self.resonance_score,
        }


@dataclass
class CreatorGoal:
    goal:        str = ""
    metric:      str = ""
    target:      str = ""
    deadline:    Optional[str] = None
    is_active:   bool = True


@dataclass
class ContentExperiment:
    experiment_tag:   str = ""
    description:      str = ""
    videos_tested:    list[str] = field(default_factory=list)
    avg_watch_pct:    float = 0.0
    avg_ctr:          float = 0.0
    conclusion:       str = ""


@dataclass
class SheetsContext:
    """
    17. AI Context Enrichment payload — everything Claude needs
    from the creator's own data.
    """
    engagement_rows:  list[EngagementRow]    = field(default_factory=list)
    goals:            list[CreatorGoal]      = field(default_factory=list)
    experiments:      list[ContentExperiment] = field(default_factory=list)
    category_summary: dict[str, dict[str, float]] = field(default_factory=dict)
    retention_trend:  list[dict[str, Any]]   = field(default_factory=list)
    top_notes:        list[str]              = field(default_factory=list)

    def to_prompt_block(self) -> str:
        """Format context as a compact markdown block for injection into Claude."""
        lines: list[str] = ["### Creator Context (Google Sheets)"]

        # Goals
        if self.goals:
            lines.append("\n**Active Goals:**")
            for g in self.goals:
                lines.append(f"- {g.goal}: target {g.target} by {g.deadline or 'N/A'}")

        # Category performance
        if self.category_summary:
            lines.append("\n**Category Avg Retention (watch%):**")
            for cat, stats in self.category_summary.items():
                lines.append(
                    f"- {cat}: watch% {stats.get('avg_watch_pct', 0):.1f}  "
                    f"CTR {stats.get('avg_ctr', 0):.1f}"
                )

        # Experiments
        if self.experiments:
            lines.append("\n**Content Experiments:**")
            for exp in self.experiments:
                lines.append(
                    f"- [{exp.experiment_tag}] {exp.description} "
                    f"→ watch% {exp.avg_watch_pct:.1f}  "
                    + (f"Conclusion: {exp.conclusion}" if exp.conclusion else "")
                )

        # Creator notes sample
        if self.top_notes:
            lines.append("\n**Creator Notes (recent):**")
            for note in self.top_notes[:5]:
                lines.append(f'- "{note}"')

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Google Sheets HTTP helper (uses service-account bearer token via google-auth)
# ---------------------------------------------------------------------------

async def _get_access_token() -> Optional[str]:
    """Obtain a short-lived OAuth2 token from the service-account JSON key."""
    try:
        import google.oauth2.service_account as sa  # type: ignore
        import google.auth.transport.requests as gatr  # type: ignore

        creds = sa.Credentials.from_service_account_file(
            settings.google_service_account_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        request = gatr.Request()
        creds.refresh(request)
        return creds.token
    except Exception as exc:
        logger.warning("Google auth failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Sheets Service
# ---------------------------------------------------------------------------

class SheetsService:
    """
    Async Google Sheets reader/writer.

    Reads engagement logs, goals, and experiments from the creator's sheet.
    Writes resonance predictions back to a Predictions tab.
    Falls back to sheets_mock.json when USE_MOCK_DATA=true or auth fails.
    """

    def __init__(self) -> None:
        self._sheet_id: Optional[str] = settings.google_sheets_id
        self._status:   str           = SourceStatus.HEALTHY

    # ------------------------------------------------------------------
    # 1. Auth guard
    # ------------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return bool(self._sheet_id)

    # ------------------------------------------------------------------
    # 15. Health monitoring
    # ------------------------------------------------------------------

    async def health(self) -> str:
        if settings.use_mock_data:
            return SourceStatus.MOCK
        if not self.is_configured:
            return SourceStatus.OFFLINE
        return self._status

    # ------------------------------------------------------------------
    # 2. Sheet Data Fetching (core)
    # ------------------------------------------------------------------

    async def fetch_engagement_log(self) -> list[EngagementRow]:
        """Fetch and normalise the engagement log tab."""
        cache_key = f"sheets_engagement:{self._sheet_id}"
        cached = await cache.get(CacheNS.GENERIC, cache_key)
        if cached is not None:
            logger.debug("Cache HIT [sheets] engagement log")
            return cached

        # 13. Mock fallback
        if settings.use_mock_data or not self.is_configured:
            return await self._mock_engagement()

        logger.info(LogMsg.SHEETS_SYNC_START)
        rows = await self._read_tab(_TAB_ENGAGEMENT)
        if rows is None:
            self._status = SourceStatus.DEGRADED
            return await self._mock_engagement()

        records = self._parse_engagement_rows(rows)
        await cache.set(CacheNS.GENERIC, cache_key, records)
        self._status = SourceStatus.HEALTHY
        return records

    async def fetch_goals(self) -> list[CreatorGoal]:
        """Fetch creator goals from the Goals tab."""
        cache_key = f"sheets_goals:{self._sheet_id}"
        cached = await cache.get(CacheNS.GENERIC, cache_key)
        if cached is not None:
            return cached

        if settings.use_mock_data or not self.is_configured:
            return []

        rows = await self._read_tab(_TAB_GOALS)
        if not rows:
            return []

        goals = self._parse_goals(rows)
        await cache.set(CacheNS.GENERIC, cache_key, goals)
        return goals

    async def fetch_experiments(self) -> list[ContentExperiment]:
        """Fetch content experiment records from the Experiments tab."""
        cache_key = f"sheets_experiments:{self._sheet_id}"
        cached = await cache.get(CacheNS.GENERIC, cache_key)
        if cached is not None:
            return cached

        if settings.use_mock_data or not self.is_configured:
            return []

        rows = await self._read_tab(_TAB_EXPERIMENTS)
        if not rows:
            return []

        experiments = self._parse_experiments(rows)
        await cache.set(CacheNS.GENERIC, cache_key, experiments)
        return experiments

    # ------------------------------------------------------------------
    # Low-level HTTP tab reader
    # ------------------------------------------------------------------

    async def _read_tab(self, tab_name: str) -> Optional[list[list[str]]]:
        """Return raw rows from a named sheet tab, or None on failure."""
        import httpx

        token = await _get_access_token()
        if not token:
            return None

        url    = f"{_SHEETS_BASE}/{self._sheet_id}/values/{tab_name}"
        headers = {"Authorization": f"Bearer {token}"}

        for attempt in range(1, MAX_API_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=settings.api_timeout) as client:
                    r = await client.get(url, headers=headers)
                    if r.status_code == 429:
                        wait = RETRY_DELAY * attempt
                        logger.warning("Sheets rate-limited — retry %d in %.1fs", attempt, wait)
                        await asyncio.sleep(wait)
                        continue
                    r.raise_for_status()
                    return r.json().get("values", [])
            except Exception as exc:
                logger.warning("Sheets read attempt %d failed: %s", attempt, exc)
                await asyncio.sleep(RETRY_DELAY * attempt)

        return None

    # ------------------------------------------------------------------
    # 9. Spreadsheet Normalisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pct(raw: str) -> float:
        """
        Normalise messy percentage values: '70%', '70', '0.70' → 70.0
        10. Returns 0.0 gracefully for missing / unparseable values.
        """
        if not raw:
            return 0.0
        cleaned = raw.strip().rstrip("%").strip()
        try:
            val = float(cleaned)
            # Detect 0–1 fractional form
            if val <= 1.0:
                val *= 100.0
            return round(min(max(val, 0.0), 100.0), 2)
        except ValueError:
            return 0.0

    @staticmethod
    def _float(raw: str, default: float = 0.0) -> float:
        try:
            return float(raw.strip().replace(",", ""))
        except (ValueError, AttributeError):
            return default

    @staticmethod
    def _str(raw: str) -> str:
        return raw.strip() if raw else ""

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_engagement_rows(self, rows: list[list[str]]) -> list[EngagementRow]:
        """
        Expected columns (first row = header):
        video_id | video_title | published_date | watch_pct | ctr |
        avg_view_duration | content_category | manual_notes | experiment_tag
        """
        if not rows:
            return []

        header = [h.lower().strip().replace(" ", "_") for h in rows[0]]
        records: list[EngagementRow] = []

        for raw_row in rows[1:]:
            # 10. Pad short rows to avoid IndexError
            row = raw_row + [""] * (len(header) - len(raw_row))
            col = dict(zip(header, row))

            records.append(EngagementRow(
                video_id          = self._str(col.get("video_id", "")),
                video_title       = self._str(col.get("video_title", "")),
                published_date    = self._str(col.get("published_date", "")) or None,
                watch_pct         = self._pct(col.get("watch_pct", col.get("watch_%", "0"))),
                ctr               = self._pct(col.get("ctr", "0")),
                avg_view_duration = self._float(col.get("avg_view_duration", "0")),
                content_category  = self._str(col.get("content_category", "General")) or "General",
                manual_notes      = self._str(col.get("manual_notes", col.get("notes", ""))),
                experiment_tag    = self._str(col.get("experiment_tag", col.get("experiment", ""))),
            ))

        return records

    def _parse_goals(self, rows: list[list[str]]) -> list[CreatorGoal]:
        if not rows:
            return []
        header = [h.lower().strip().replace(" ", "_") for h in rows[0]]
        goals: list[CreatorGoal] = []
        for raw_row in rows[1:]:
            row = raw_row + [""] * (len(header) - len(raw_row))
            col = dict(zip(header, row))
            goals.append(CreatorGoal(
                goal     = self._str(col.get("goal", "")),
                metric   = self._str(col.get("metric", "")),
                target   = self._str(col.get("target", "")),
                deadline = self._str(col.get("deadline", "")) or None,
                is_active = col.get("is_active", "true").lower().strip() not in ("false", "no", "0"),
            ))
        return [g for g in goals if g.goal]

    def _parse_experiments(self, rows: list[list[str]]) -> list[ContentExperiment]:
        if not rows:
            return []
        header = [h.lower().strip().replace(" ", "_") for h in rows[0]]
        experiments: list[ContentExperiment] = []
        for raw_row in rows[1:]:
            row = raw_row + [""] * (len(header) - len(raw_row))
            col = dict(zip(header, row))
            video_ids_raw = col.get("video_ids", col.get("videos", ""))
            video_ids = [v.strip() for v in re.split(r"[,;]", video_ids_raw) if v.strip()]
            experiments.append(ContentExperiment(
                experiment_tag = self._str(col.get("experiment_tag", col.get("tag", ""))),
                description    = self._str(col.get("description", "")),
                videos_tested  = video_ids,
                avg_watch_pct  = self._pct(col.get("avg_watch_pct", col.get("watch_pct", "0"))),
                avg_ctr        = self._pct(col.get("avg_ctr", col.get("ctr", "0"))),
                conclusion     = self._str(col.get("conclusion", col.get("result", ""))),
            ))
        return [e for e in experiments if e.experiment_tag]

    # ------------------------------------------------------------------
    # 3. Watch Percentage lookup (used by resonance scorer)
    # ------------------------------------------------------------------

    async def watch_pct_map(self) -> dict[str, float]:
        """Return {video_id: watch_pct} for all rows in the engagement log."""
        rows = await self.fetch_engagement_log()
        return {r.video_id: r.watch_pct for r in rows if r.video_id}

    # ------------------------------------------------------------------
    # 5. Category performance summary
    # ------------------------------------------------------------------

    async def category_summary(self) -> dict[str, dict[str, float]]:
        """
        Return per-category avg watch_pct and CTR — feeds insight_engine.
        """
        cache_key = f"sheets_cat_summary:{self._sheet_id}"
        cached = await cache.get(CacheNS.ANALYTICS, cache_key)
        if cached is not None:
            return cached

        rows = await self.fetch_engagement_log()
        buckets: dict[str, list[EngagementRow]] = {}
        for row in rows:
            buckets.setdefault(row.content_category, []).append(row)

        summary: dict[str, dict[str, float]] = {}
        for cat, items in buckets.items():
            watch_vals = [r.watch_pct for r in items if r.watch_pct > 0]
            ctr_vals   = [r.ctr for r in items if r.ctr > 0]
            summary[cat] = {
                "video_count":   len(items),
                "avg_watch_pct": round(sum(watch_vals) / len(watch_vals), 2) if watch_vals else 0.0,
                "avg_ctr":       round(sum(ctr_vals) / len(ctr_vals), 2) if ctr_vals else 0.0,
            }

        await cache.set(CacheNS.ANALYTICS, cache_key, summary)
        return summary

    # ------------------------------------------------------------------
    # 12. Retention Trend
    # ------------------------------------------------------------------

    async def retention_trend(self) -> list[dict[str, Any]]:
        """Time-series of watch_pct per published_date, sorted ascending."""
        rows = await self.fetch_engagement_log()
        dated = [r for r in rows if r.published_date and r.watch_pct > 0]
        dated.sort(key=lambda r: r.published_date or "")
        return [
            {"date": r.published_date, "watch_pct": r.watch_pct, "video_id": r.video_id}
            for r in dated
        ]

    # ------------------------------------------------------------------
    # 4. Manual Notes — extract top insights for AI context
    # ------------------------------------------------------------------

    async def top_notes(self, n: int = 10) -> list[str]:
        rows = await self.fetch_engagement_log()
        return [r.manual_notes for r in rows if r.manual_notes.strip()][:n]

    # ------------------------------------------------------------------
    # 17. AI Context Enrichment — full SheetsContext for Claude
    # ------------------------------------------------------------------

    async def build_ai_context(self) -> SheetsContext:
        """Aggregate everything into one object for injection into Claude."""
        cache_key = f"sheets_ai_context:{self._sheet_id}"
        cached = await cache.get(CacheNS.INSIGHT, cache_key)
        if cached is not None:
            return cached

        # Fetch all tabs concurrently
        rows, goals, experiments = await asyncio.gather(
            self.fetch_engagement_log(),
            self.fetch_goals(),
            self.fetch_experiments(),
        )

        cat_summary = await self.category_summary()
        ret_trend   = await self.retention_trend()
        notes       = await self.top_notes()

        context = SheetsContext(
            engagement_rows  = rows,
            goals            = [g for g in goals if g.is_active],
            experiments      = experiments,
            category_summary = cat_summary,
            retention_trend  = ret_trend,
            top_notes        = notes,
        )

        await cache.set(CacheNS.INSIGHT, cache_key, context)
        return context

    # ------------------------------------------------------------------
    # 11. Cross-Source Mapping — align sheet rows to YouTube video IDs
    # ------------------------------------------------------------------

    async def engagement_by_video_id(self) -> dict[str, EngagementRow]:
        """Return {video_id: EngagementRow} for fast Coral JOIN lookups."""
        rows = await self.fetch_engagement_log()
        return {r.video_id: r for r in rows if r.video_id}

    # ------------------------------------------------------------------
    # Write-back: Predictions tab
    # ------------------------------------------------------------------

    async def write_predictions(
        self,
        predictions: list[dict[str, Any]],
    ) -> bool:
        """
        Write resonance score predictions back to the Predictions sheet tab.
        Format: [video_id, video_title, predicted_score, predicted_date]

        Returns True on success, False on failure (never raises).
        """
        if settings.use_mock_data or not self.is_configured:
            logger.info("Skipping sheet write-back (mock/unconfigured)")
            return False

        import httpx

        token = await _get_access_token()
        if not token:
            return False

        logger.info(LogMsg.SHEETS_WRITE_BACK)

        header = [["video_id", "video_title", "predicted_score", "updated_at"]]
        now    = datetime.now(timezone.utc).isoformat()
        body_rows = [
            [
                p.get("video_id", ""),
                p.get("video_title", ""),
                str(round(p.get("predicted_score", 0), 2)),
                now,
            ]
            for p in predictions
        ]

        payload = {"values": header + body_rows}
        url     = (
            f"{_SHEETS_BASE}/{self._sheet_id}/values/"
            f"{_TAB_PREDICTIONS}!A1:D{len(body_rows) + 1}"
            "?valueInputOption=USER_ENTERED"
        )
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=settings.api_timeout) as client:
                r = await client.put(url, headers=headers, json=payload)
                r.raise_for_status()
                logger.info("Wrote %d predictions to Google Sheets", len(predictions))
                return True
        except Exception as exc:
            logger.warning("Failed to write predictions to Sheets: %s", exc)
            return False

    # ------------------------------------------------------------------
    # 13. Mock Mode Fallback
    # ------------------------------------------------------------------

    async def _mock_engagement(self) -> list[EngagementRow]:
        data = await cache.get_mock("sheets")
        if not data:
            logger.warning("No mock Sheets data available — returning empty list")
            return []

        rows: list[EngagementRow] = []
        for raw in data.get("engagement_log", []):
            rows.append(EngagementRow(
                video_id          = raw.get("video_id", ""),
                video_title       = raw.get("video_title", ""),
                published_date    = raw.get("published_date"),
                watch_pct         = self._pct(str(raw.get("watch_pct", "0"))),
                ctr               = self._pct(str(raw.get("ctr", "0"))),
                avg_view_duration = self._float(str(raw.get("avg_view_duration", "0"))),
                content_category  = raw.get("content_category", "General"),
                manual_notes      = raw.get("manual_notes", ""),
                experiment_tag    = raw.get("experiment_tag", ""),
                resonance_score   = raw.get("resonance_score"),
            ))

        logger.info("Loaded %d mock Sheets rows", len(rows))
        return rows


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
sheets_service = SheetsService()