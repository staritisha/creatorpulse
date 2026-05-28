-- =============================================================================
-- coral/queries/resonance.sql
-- CreatorPulse · Signature Intelligence Query
-- =============================================================================
-- Role: The single source of truth for Creator Resonance.
--       Joins YouTube performance + Discord community activity + Google Sheets
--       retention data into one measurable per-video signal.
--
-- Powers:  routes/analytics.py · ai/insight_engine.py · scoring/resonance_score.py
-- Params:  :timeframe_days  — lookback window (7 | 30 | 90), default 30
--          :topic_filter    — optional topic/tag string filter, NULL = all
--          :top_n           — max rows returned for ranking queries, default 20
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SECTION 1 · Raw source CTEs
-- Pull the latest data from each Coral source individually before joining.
-- Keeping each CTE isolated makes mock-mode swapping straightforward — swap
-- the CTE body for SELECT … FROM mock_<source> without touching join logic.
-- ---------------------------------------------------------------------------

WITH

-- 1a. YouTube videos published within the requested timeframe
yt_videos AS (
    SELECT
        v.video_id,
        v.title,
        v.topic,
        v.tag,
        v.published_at,
        v.views,
        v.likes,
        v.comments,
        v.ctr,                          -- click-through rate (0–1 float)
        v.watch_pct,                    -- average view duration / duration (0–100)
        -- Normalised YouTube engagement quality: (likes + comments) / max(views, 1)
        ROUND(
            (v.likes + v.comments) * 1.0 / GREATEST(v.views, 1),
            4
        )                               AS engagement_ratio
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
      AND (:topic_filter IS NULL OR v.topic ILIKE '%' || :topic_filter || '%')
),

-- 1b. Discord message activity — aggregated per topic keyword and per day
-- Provides two signals: total volume (resonance weight) and spike detection.
disc_activity AS (
    SELECT
        m.keyword                       AS topic,
        COUNT(*)                        AS msg_count,
        COUNT(DISTINCT m.author_id)     AS unique_authors,
        SUM(m.reply_count)              AS reply_chains,
        -- Baseline: average daily messages across the full window
        ROUND(
            COUNT(*) * 1.0 / GREATEST(:timeframe_days, 1),
            2
        )                               AS daily_baseline,
        -- Spike ratio: peak day vs baseline (>3 = community burst)
        ROUND(
            MAX(m.daily_count) * 1.0 / GREATEST(
                COUNT(*) * 1.0 / GREATEST(:timeframe_days, 1), 1
            ),
            2
        )                               AS spike_ratio
    FROM discord.messages AS m
    WHERE m.created_at >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY m.keyword
),

-- 1c. Google Sheets engagement log — retention + watch quality per video
sheets_engagement AS (
    SELECT
        e.video_title,
        e.video_id,
        e.watch_pct                     AS sheet_watch_pct,
        e.date                          AS log_date,
        -- Row-level engagement quality cross-check (supplements YouTube field)
        ROUND(
            (e.likes + e.comments) * 1.0 / GREATEST(e.views, 1),
            4
        )                               AS sheet_engagement_ratio
    FROM sheets.engagement_log AS e
    WHERE e.date >= NOW() - INTERVAL ':timeframe_days' DAY
),

-- ---------------------------------------------------------------------------
-- SECTION 2 · Cross-Source Resonance JOIN  ← MOST IMPORTANT
-- Merge all three sources per video.  LEFT JOINs so a video is never dropped
-- when Discord or Sheets data is missing; NULLs are coalesced to safe zeros.
-- ---------------------------------------------------------------------------

resonance_raw AS (
    SELECT
        -- ── Identity ──────────────────────────────────────────────────────
        yt.video_id,
        yt.title,
        yt.topic,
        yt.tag,
        yt.published_at,

        -- ── YouTube signals ────────────────────────────────────────────────
        yt.views,
        yt.likes,
        yt.comments,
        yt.ctr,
        -- Prefer Sheets watch_pct when available (more granular); fall back to YouTube
        COALESCE(se.sheet_watch_pct, yt.watch_pct, 0.0)  AS watch_pct,
        yt.engagement_ratio,

        -- ── Discord signals ─────────────────────────────────────────────────
        COALESCE(da.msg_count, 0)       AS discord_msg_count,
        COALESCE(da.unique_authors, 0)  AS discord_unique_authors,
        COALESCE(da.reply_chains, 0)    AS discord_reply_chains,
        COALESCE(da.daily_baseline, 0)  AS discord_daily_baseline,
        -- Community spike: ratio > 3 flags a burst post-upload
        COALESCE(da.spike_ratio, 1.0)   AS community_spike_ratio,

        -- ── Sheets signals ──────────────────────────────────────────────────
        COALESCE(se.sheet_engagement_ratio, yt.engagement_ratio) AS quality_engagement_ratio,

        -- ── Source attribution flags (used by frontend pill badges) ─────────
        CASE WHEN yt.video_id IS NOT NULL     THEN 1 ELSE 0 END  AS src_youtube,
        CASE WHEN da.topic IS NOT NULL        THEN 1 ELSE 0 END  AS src_discord,
        CASE WHEN se.video_id IS NOT NULL     THEN 1 ELSE 0 END  AS src_sheets

    FROM yt_videos AS yt
    LEFT JOIN disc_activity AS da
        ON  yt.topic ILIKE '%' || da.topic || '%'           -- fuzzy topic match
    LEFT JOIN sheets_engagement AS se
        ON  se.video_id = yt.video_id
         OR se.video_title ILIKE yt.title                   -- title fallback
),

-- ---------------------------------------------------------------------------
-- SECTION 3 · Weighted Score Preparation
-- Compute each of the three component scores using the formula from
-- scoring/resonance_score.py.  Weights stored here mirror constants.py:
--   watch_pct component  → weight 0.40
--   discord component    → weight 0.40
--   engagement component → weight 0.20
-- ---------------------------------------------------------------------------

resonance_scored AS (
    SELECT
        r.*,

        -- Component A: YouTube audience retention (0–40 pts)
        -- watch_pct is 0–100; cap at 40 points
        ROUND(LEAST(r.watch_pct / 100.0, 1.0) * 40.0, 2)  AS score_watch,

        -- Component B: Community discussion strength (0–40 pts)
        -- Normalise discord_msg_count against a baseline of 50 msgs per video
        ROUND(LEAST(r.discord_msg_count / 50.0, 1.0) * 40.0, 2) AS score_discord,

        -- Component C: Engagement quality (0–20 pts)
        -- Combined ratio of watch% + comment density, capped at 20 pts
        ROUND(
            (
                (r.watch_pct / 100.0)
                + LEAST(r.comments * 1.0 / GREATEST(r.views, 1), 1.0)
            ) * 10.0,
            2
        )  AS score_engagement,

        -- ── Underperformance signal detection ─────────────────────────────
        -- Flag "false popularity": high views but weak retention or low Discord
        CASE
            WHEN r.views > 50000
             AND r.watch_pct < 30
            THEN 1 ELSE 0
        END  AS flag_high_views_low_retention,

        CASE
            WHEN r.views > 50000
             AND COALESCE(r.discord_msg_count, 0) < 5
            THEN 1 ELSE 0
        END  AS flag_high_views_low_community,

        -- Community burst flag (post-upload spike >3× baseline)
        CASE
            WHEN r.community_spike_ratio >= 3.0
            THEN 1 ELSE 0
        END  AS flag_community_burst

    FROM resonance_raw AS r
),

-- ---------------------------------------------------------------------------
-- SECTION 4 · Final Resonance Score + Ranking
-- Aggregate the three components into the 0–100 score and rank videos.
-- ---------------------------------------------------------------------------

resonance_final AS (
    SELECT
        s.*,

        -- Total resonance score (0–100)
        ROUND(
            s.score_watch + s.score_discord + s.score_engagement,
            1
        )  AS resonance_score,

        -- Tier classification for UI colour coding
        CASE
            WHEN (s.score_watch + s.score_discord + s.score_engagement) >= 80 THEN 'high'
            WHEN (s.score_watch + s.score_discord + s.score_engagement) >= 50 THEN 'medium'
            ELSE 'low'
        END  AS resonance_tier,

        -- Video-level rank (1 = highest resonance)
        ROW_NUMBER() OVER (
            ORDER BY (s.score_watch + s.score_discord + s.score_engagement) DESC
        )  AS resonance_rank,

        -- Topic-level rank (rank within each topic group)
        ROW_NUMBER() OVER (
            PARTITION BY s.topic
            ORDER BY (s.score_watch + s.score_discord + s.score_engagement) DESC
        )  AS topic_rank

    FROM resonance_scored AS s
),

-- ---------------------------------------------------------------------------
-- SECTION 5 · Topic-Level Resonance Aggregation
-- Answers "What topic should I create next?" by aggregating video scores
-- per topic to surface which subjects drive deepest audience connection.
-- ---------------------------------------------------------------------------

topic_resonance AS (
    SELECT
        f.topic,
        COUNT(*)                                            AS video_count,
        ROUND(AVG(f.resonance_score), 1)                   AS avg_resonance,
        ROUND(MAX(f.resonance_score), 1)                   AS peak_resonance,
        SUM(f.discord_msg_count)                           AS total_discord_msgs,
        ROUND(AVG(f.watch_pct), 1)                         AS avg_watch_pct,
        ROUND(AVG(f.engagement_ratio), 4)                  AS avg_engagement_ratio,
        -- Community interest strength for this topic
        ROUND(AVG(f.community_spike_ratio), 2)             AS avg_spike_ratio,
        -- Row for final JOIN back
        ROW_NUMBER() OVER (ORDER BY AVG(f.resonance_score) DESC) AS topic_rank
    FROM resonance_final AS f
    GROUP BY f.topic
),

-- ---------------------------------------------------------------------------
-- SECTION 6 · Weekly Trend CTE
-- Tracks resonance movement over time — powers "Am I improving?" insights
-- and the trend chart on the analytics dashboard.
-- ---------------------------------------------------------------------------

weekly_trend AS (
    SELECT
        DATE_TRUNC('week', f.published_at)                 AS week_start,
        f.topic,
        f.tag,
        ROUND(AVG(f.resonance_score), 1)                   AS avg_resonance,
        COUNT(*)                                           AS videos_published,
        SUM(f.discord_msg_count)                           AS total_community_msgs,
        ROUND(AVG(f.watch_pct), 1)                         AS avg_watch_pct
    FROM resonance_final AS f
    GROUP BY DATE_TRUNC('week', f.published_at), f.topic, f.tag
    ORDER BY week_start DESC
)

-- =============================================================================
-- FINAL SELECT — Claude-ready + Dashboard-ready output
-- =============================================================================
-- Returns one row per video, enriched with:
--   · Resonance score + tier + rank
--   · All weighted input components (for scoring/resonance_score.py)
--   · Underperformance and community-burst flags (for ai/detectors.py)
--   · Topic-level aggregates joined back (for topic recommendations)
--   · Source attribution flags (for frontend pill badges)
--   · Weekly trend join (for trend chart)
-- Ordered by resonance_rank so the first rows are the most impressive for
-- the demo and for Claude's context window (top content first).
-- =============================================================================

SELECT
    -- ── Identity ────────────────────────────────────────────────────────────
    f.video_id,
    f.title,
    f.topic,
    f.tag,
    f.published_at,

    -- ── Raw signals (inputs to scoring/resonance_score.py) ─────────────────
    f.views,
    f.likes,
    f.comments,
    f.ctr,
    f.watch_pct,
    f.engagement_ratio,
    f.discord_msg_count,
    f.discord_unique_authors,
    f.discord_reply_chains,
    f.community_spike_ratio,
    f.quality_engagement_ratio,

    -- ── Weighted score components ───────────────────────────────────────────
    f.score_watch,
    f.score_discord,
    f.score_engagement,

    -- ── Final resonance score + classification ──────────────────────────────
    f.resonance_score,
    f.resonance_tier,
    f.resonance_rank,
    f.topic_rank,

    -- ── Underperformance + burst detection flags ────────────────────────────
    f.flag_high_views_low_retention,
    f.flag_high_views_low_community,
    f.flag_community_burst,

    -- ── Topic-level aggregates (denormalised for single-query Claude context) ──
    tr.avg_resonance                    AS topic_avg_resonance,
    tr.video_count                      AS topic_video_count,
    tr.avg_watch_pct                    AS topic_avg_watch_pct,
    tr.total_discord_msgs               AS topic_total_discord_msgs,
    tr.topic_rank                       AS topic_overall_rank,

    -- ── Source attribution (drives frontend "YouTube · Discord · Sheets" badges)
    f.src_youtube,
    f.src_discord,
    f.src_sheets,
    -- Human-readable source string for Claude context
    CONCAT_WS(' · ',
        CASE WHEN f.src_youtube = 1 THEN 'YouTube'  END,
        CASE WHEN f.src_discord = 1 THEN 'Discord'  END,
        CASE WHEN f.src_sheets  = 1 THEN 'Sheets'   END
    )                                   AS source_attribution,

    -- ── Trend context (most recent week for this topic) ─────────────────────
    wt.avg_resonance                    AS topic_trend_latest_week_avg,
    wt.week_start                       AS trend_week

FROM resonance_final AS f

-- Join topic-level aggregates
LEFT JOIN topic_resonance AS tr
    ON tr.topic = f.topic

-- Join most-recent weekly trend row for this topic
LEFT JOIN (
    SELECT DISTINCT ON (topic)
        topic, avg_resonance, week_start
    FROM weekly_trend
    ORDER BY topic, week_start DESC
) AS wt
    ON wt.topic = f.topic

-- Limit result set for performance + clean AI context window
WHERE f.resonance_rank <= :top_n

ORDER BY f.resonance_rank ASC;
