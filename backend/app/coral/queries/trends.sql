-- =============================================================================
-- coral/queries/trends.sql
-- CreatorPulse · Creator Evolution Intelligence Query
-- =============================================================================
-- Role: Answers "What is CHANGING over time?"
--       Unlike resonance.sql (current score snapshot) and engagement.sql
--       (single-period activity), this query tracks growth momentum,
--       topic evolution, community acceleration, and retention improvement
--       across configurable time windows.
--
-- Powers:  routes/analytics.py · ai/insight_engine.py · scoring/growth_predictor.py
-- Params:  :timeframe_days   — total lookback window (7 | 30 | 90 | 180 | 365), default 90
--          :bucket           — time grouping ('day' | 'week' | 'month'), default 'week'
--          :topic_filter     — optional topic/tag string, NULL = all topics
--          :top_n            — max topic rows in high-momentum output, default 10
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SECTION 1 · Raw time-series CTEs
-- Each CTE pulls one source, bucketed by :bucket period, so later JOINs line
-- up on the same time key.  Swap CTE bodies for mock_ tables if APIs fail —
-- no join logic changes needed (mock mode compatibility).
-- ---------------------------------------------------------------------------

WITH

-- 1a. YouTube videos — one row per publish-period bucket
yt_by_period AS (
    SELECT
        DATE_TRUNC(:bucket, v.published_at)   AS period,
        v.topic,
        v.tag,
        COUNT(*)                              AS videos_published,
        SUM(v.views)                          AS total_views,
        SUM(v.likes)                          AS total_likes,
        SUM(v.comments)                       AS total_comments,
        -- Retention quality: average watch % across all videos in the bucket
        ROUND(AVG(v.watch_pct), 2)            AS avg_watch_pct,
        -- Engagement quality ratio for the period
        ROUND(
            SUM(v.likes + v.comments) * 1.0
            / GREATEST(SUM(v.views), 1),
            4
        )                                     AS period_engagement_ratio,
        -- CTR average
        ROUND(AVG(v.ctr), 4)                  AS avg_ctr
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
      AND (:topic_filter IS NULL OR v.topic ILIKE '%' || :topic_filter || '%')
    GROUP BY DATE_TRUNC(:bucket, v.published_at), v.topic, v.tag
),

-- 1b. Discord messages — activity bucketed by period and matched keyword/topic
disc_by_period AS (
    SELECT
        DATE_TRUNC(:bucket, m.created_at)     AS period,
        m.keyword                             AS topic,
        COUNT(*)                              AS total_messages,
        COUNT(DISTINCT m.author_id)           AS active_members,
        SUM(m.reply_count)                    AS total_replies,
        -- Spike days: days where message count exceeded 3× the period's daily avg
        COUNT(DISTINCT CASE
            WHEN m.daily_count > (
                COUNT(*) OVER (PARTITION BY DATE_TRUNC(:bucket, m.created_at), m.keyword)
                * 3.0
                / GREATEST(
                    DATE_PART('day', DATE_TRUNC(:bucket, NOW())
                              - DATE_TRUNC(:bucket, m.created_at) + INTERVAL '1 ' || :bucket),
                    1
                )
            ) THEN DATE(m.created_at)
        END)                                  AS spike_days
    FROM discord.messages AS m
    WHERE m.created_at >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY DATE_TRUNC(:bucket, m.created_at), m.keyword
),

-- 1c. Google Sheets engagement log — retention data bucketed by period
sheets_by_period AS (
    SELECT
        DATE_TRUNC(:bucket, e.date)           AS period,
        e.video_title,
        -- Average watch% from the retention log (most accurate source)
        ROUND(AVG(e.watch_pct), 2)            AS avg_watch_pct,
        ROUND(
            AVG((e.likes + e.comments) * 1.0 / GREATEST(e.views, 1)),
            4
        )                                     AS avg_quality_engagement
    FROM sheets.engagement_log AS e
    WHERE e.date >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY DATE_TRUNC(:bucket, e.date), e.video_title
),

-- ---------------------------------------------------------------------------
-- SECTION 2 · Cross-Source Trend JOIN
-- Merge all three sources on (period, topic) — the spine of the trend view.
-- LEFT JOINs preserve every YouTube period even when Discord or Sheets data
-- is absent for that period (e.g., a quiet week in the community).
-- ---------------------------------------------------------------------------

trends_raw AS (
    SELECT
        yt.period,
        yt.topic,
        yt.tag,

        -- ── YouTube signals ──────────────────────────────────────────────
        yt.videos_published,
        yt.total_views,
        yt.total_likes,
        yt.total_comments,
        yt.avg_watch_pct,
        yt.period_engagement_ratio,
        yt.avg_ctr,

        -- ── Discord signals ──────────────────────────────────────────────
        COALESCE(da.total_messages, 0)        AS discord_messages,
        COALESCE(da.active_members, 0)        AS discord_active_members,
        COALESCE(da.total_replies, 0)         AS discord_replies,
        COALESCE(da.spike_days, 0)            AS discord_spike_days,

        -- ── Sheets retention signals ─────────────────────────────────────
        -- Aggregate across all video titles published in this period
        ROUND(AVG(se.avg_watch_pct), 2)       AS sheets_avg_watch_pct,
        ROUND(AVG(se.avg_quality_engagement), 4) AS sheets_avg_quality_engagement,

        -- ── Source attribution ───────────────────────────────────────────
        CASE WHEN yt.topic IS NOT NULL        THEN 1 ELSE 0 END AS src_youtube,
        CASE WHEN da.topic IS NOT NULL        THEN 1 ELSE 0 END AS src_discord,
        CASE WHEN se.video_title IS NOT NULL  THEN 1 ELSE 0 END AS src_sheets

    FROM yt_by_period AS yt
    LEFT JOIN disc_by_period AS da
        ON  da.period = yt.period
        AND yt.topic ILIKE '%' || da.topic || '%'
    LEFT JOIN sheets_by_period AS se
        ON  se.period = yt.period
    GROUP BY
        yt.period, yt.topic, yt.tag,
        yt.videos_published, yt.total_views, yt.total_likes, yt.total_comments,
        yt.avg_watch_pct, yt.period_engagement_ratio, yt.avg_ctr,
        da.total_messages, da.active_members, da.total_replies, da.spike_days,
        da.topic, se.video_title
),

-- ---------------------------------------------------------------------------
-- SECTION 3 · Period-over-Period Change Calculation
-- Uses LAG window functions to compute absolute and percentage deltas for
-- every key metric versus the previous period bucket.
-- These deltas are the core signals for growth_predictor.py and Claude.
-- ---------------------------------------------------------------------------

trends_delta AS (
    SELECT
        t.*,

        -- ── Watch % trend (retention improvement) ────────────────────────
        -- Prefer Sheets retention when available, fall back to YouTube avg
        COALESCE(t.sheets_avg_watch_pct, t.avg_watch_pct)   AS effective_watch_pct,

        ROUND(
            COALESCE(t.sheets_avg_watch_pct, t.avg_watch_pct)
            - LAG(COALESCE(t.sheets_avg_watch_pct, t.avg_watch_pct))
                  OVER (PARTITION BY t.topic ORDER BY t.period),
            2
        )  AS watch_pct_delta,

        -- ── Views trend ──────────────────────────────────────────────────
        t.total_views
        - LAG(t.total_views) OVER (PARTITION BY t.topic ORDER BY t.period)
            AS views_delta,

        ROUND(
            (t.total_views - LAG(t.total_views)
                OVER (PARTITION BY t.topic ORDER BY t.period))
            * 100.0
            / GREATEST(LAG(t.total_views)
                OVER (PARTITION BY t.topic ORDER BY t.period), 1),
            1
        )  AS views_pct_change,

        -- ── Engagement trend ─────────────────────────────────────────────
        ROUND(
            t.period_engagement_ratio
            - LAG(t.period_engagement_ratio)
                  OVER (PARTITION BY t.topic ORDER BY t.period),
            4
        )  AS engagement_ratio_delta,

        -- ── Community (Discord) trend ─────────────────────────────────────
        t.discord_messages
        - LAG(t.discord_messages)
              OVER (PARTITION BY t.topic ORDER BY t.period)
            AS discord_messages_delta,

        ROUND(
            (t.discord_messages - LAG(t.discord_messages)
                OVER (PARTITION BY t.topic ORDER BY t.period))
            * 100.0
            / GREATEST(LAG(t.discord_messages)
                OVER (PARTITION BY t.topic ORDER BY t.period), 1),
            1
        )  AS discord_pct_change,

        -- ── Upload consistency ───────────────────────────────────────────
        t.videos_published
        - LAG(t.videos_published)
              OVER (PARTITION BY t.topic ORDER BY t.period)
            AS upload_frequency_delta,

        -- Flag: upload gap (zero videos after a period where there were some)
        CASE
            WHEN t.videos_published = 0
             AND LAG(t.videos_published)
                     OVER (PARTITION BY t.topic ORDER BY t.period) > 0
            THEN 1 ELSE 0
        END  AS flag_upload_gap

    FROM trends_raw AS t
),

-- ---------------------------------------------------------------------------
-- SECTION 4 · Resonance Trend — rolling resonance score per period
-- Mirrors the formula from resonance.sql / scoring/resonance_score.py so
-- growth_predictor.py has a consistent time-series to fit against.
-- ---------------------------------------------------------------------------

resonance_trend AS (
    SELECT
        d.period,
        d.topic,
        d.tag,

        -- Resonance score for this period (same weighted formula)
        ROUND(
            -- Component A: retention (0–40)
            LEAST(COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
            -- Component B: community (0–40)
          + LEAST(d.discord_messages / 50.0, 1.0) * 40.0
            -- Component C: engagement quality (0–20)
          + (
                (COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0)
              + LEAST(d.total_comments * 1.0 / GREATEST(d.total_views, 1), 1.0)
            ) * 10.0,
            1
        )  AS period_resonance_score,

        -- Change in resonance vs previous period (for "Am I improving?" insight)
        ROUND(
            (
                LEAST(COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
              + LEAST(d.discord_messages / 50.0, 1.0) * 40.0
              + (
                    (COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0)
                  + LEAST(d.total_comments * 1.0 / GREATEST(d.total_views, 1), 1.0)
                ) * 10.0
            )
            - LAG(
                LEAST(COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
              + LEAST(d.discord_messages / 50.0, 1.0) * 40.0
              + (
                    (COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0)
                  + LEAST(d.total_comments * 1.0 / GREATEST(d.total_views, 1), 1.0)
                ) * 10.0
              ) OVER (PARTITION BY d.topic ORDER BY d.period),
            1
        )  AS resonance_delta,

        -- Momentum classification for AI context (rising / stable / declining)
        CASE
            WHEN (
                    LEAST(COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
                  + LEAST(d.discord_messages / 50.0, 1.0) * 40.0
                  + (
                        (COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0)
                      + LEAST(d.total_comments * 1.0 / GREATEST(d.total_views, 1), 1.0)
                    ) * 10.0
                 )
                 - LAG(
                    LEAST(COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
                  + LEAST(d.discord_messages / 50.0, 1.0) * 40.0
                  + (
                        (COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0)
                      + LEAST(d.total_comments * 1.0 / GREATEST(d.total_views, 1), 1.0)
                    ) * 10.0
                  ) OVER (PARTITION BY d.topic ORDER BY d.period)
                 > 5
            THEN 'rising'
            WHEN (
                    LEAST(COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
                  + LEAST(d.discord_messages / 50.0, 1.0) * 40.0
                  + (
                        (COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0)
                      + LEAST(d.total_comments * 1.0 / GREATEST(d.total_views, 1), 1.0)
                    ) * 10.0
                 )
                 - LAG(
                    LEAST(COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
                  + LEAST(d.discord_messages / 50.0, 1.0) * 40.0
                  + (
                        (COALESCE(d.sheets_avg_watch_pct, d.avg_watch_pct, 0) / 100.0)
                      + LEAST(d.total_comments * 1.0 / GREATEST(d.total_views, 1), 1.0)
                    ) * 10.0
                  ) OVER (PARTITION BY d.topic ORDER BY d.period)
                 < -5
            THEN 'declining'
            ELSE 'stable'
        END  AS momentum_label

    FROM trends_delta AS d
),

-- ---------------------------------------------------------------------------
-- SECTION 5 · High-Momentum Topic Detection
-- Identifies the fastest-growing topics by averaging resonance delta across
-- all periods.  Top-N topics by avg delta → "What to create more of" answer.
-- ---------------------------------------------------------------------------

topic_momentum AS (
    SELECT
        rt.topic,
        rt.tag,
        COUNT(*)                              AS periods_tracked,
        ROUND(AVG(rt.period_resonance_score), 1) AS avg_resonance,
        ROUND(AVG(rt.resonance_delta), 1)     AS avg_resonance_delta,
        ROUND(MAX(rt.period_resonance_score), 1) AS peak_resonance,
        ROUND(MIN(rt.period_resonance_score), 1) AS trough_resonance,
        -- Consistent momentum: positive delta in majority of periods
        SUM(CASE WHEN rt.momentum_label = 'rising'   THEN 1 ELSE 0 END) AS rising_periods,
        SUM(CASE WHEN rt.momentum_label = 'declining' THEN 1 ELSE 0 END) AS declining_periods,
        -- Overall topic trajectory
        CASE
            WHEN AVG(rt.resonance_delta) > 3   THEN 'rising'
            WHEN AVG(rt.resonance_delta) < -3  THEN 'declining'
            ELSE 'stable'
        END  AS topic_trajectory,
        ROW_NUMBER() OVER (ORDER BY AVG(rt.resonance_delta) DESC) AS momentum_rank
    FROM resonance_trend AS rt
    GROUP BY rt.topic, rt.tag
),

-- ---------------------------------------------------------------------------
-- SECTION 6 · Predictive Signal Aggregation
-- Collects ordered resonance scores per topic for growth_predictor.py's
-- numpy polyfit.  Returns the last 10 scores as an ordered series string
-- so the Python layer can deserialise and fit without re-querying.
-- ---------------------------------------------------------------------------

predictor_feed AS (
    SELECT
        rt.topic,
        -- Ordered resonance scores (oldest → newest) as array aggregate
        STRING_AGG(
            CAST(rt.period_resonance_score AS TEXT),
            ','
            ORDER BY rt.period ASC
        )  AS resonance_series,
        -- Most recent score (baseline for the prediction)
        MAX(rt.period_resonance_score) FILTER (
            WHERE rt.period = MAX(rt.period) OVER (PARTITION BY rt.topic)
        )  AS latest_score,
        -- Average delta → trend slope input
        ROUND(AVG(rt.resonance_delta), 2)  AS avg_delta,
        COUNT(*)                           AS data_points
    FROM resonance_trend AS rt
    GROUP BY rt.topic
    HAVING COUNT(*) >= 2                   -- need at least 2 points for a trend
)

-- =============================================================================
-- FINAL SELECT — Claude-ready + Dashboard-ready trend output
-- =============================================================================
-- Returns one row per (period, topic) with:
--   · All raw metric deltas (for time-series charts)
--   · Resonance score + delta + momentum label (for AI insight)
--   · Topic momentum aggregates joined back (for "what to make next")
--   · Predictor feed (for growth_predictor.py)
--   · Upload consistency + community spike flags (for alert insights)
--   · Source attribution (for frontend badges)
-- Ordered period DESC so the most recent data arrives first in Claude's
-- context window and in the dashboard trend chart.
-- =============================================================================

SELECT
    -- ── Identity & time ─────────────────────────────────────────────────────
    td.period,
    td.topic,
    td.tag,

    -- ── Upload consistency ───────────────────────────────────────────────────
    td.videos_published,
    td.upload_frequency_delta,
    td.flag_upload_gap,

    -- ── YouTube raw metrics ─────────────────────────────────────────────────
    td.total_views,
    td.views_delta,
    td.views_pct_change,
    td.total_likes,
    td.total_comments,
    td.avg_ctr,

    -- ── Retention quality (audience retention trend) ─────────────────────────
    td.effective_watch_pct,
    td.watch_pct_delta,

    -- ── Engagement quality trend ─────────────────────────────────────────────
    td.period_engagement_ratio,
    td.engagement_ratio_delta,

    -- ── Community (Discord) trend ────────────────────────────────────────────
    td.discord_messages,
    td.discord_messages_delta,
    td.discord_pct_change,
    td.discord_active_members,
    td.discord_replies,
    td.discord_spike_days,

    -- ── Resonance trend (from resonance_trend CTE) ──────────────────────────
    rt.period_resonance_score,
    rt.resonance_delta,
    rt.momentum_label,

    -- ── Topic momentum aggregates (denormalised for single-query AI context) ──
    tm.avg_resonance                    AS topic_avg_resonance,
    tm.avg_resonance_delta              AS topic_avg_resonance_delta,
    tm.topic_trajectory,
    tm.momentum_rank                    AS topic_momentum_rank,
    tm.rising_periods                   AS topic_rising_periods,
    tm.declining_periods                AS topic_declining_periods,

    -- ── Predictor feed (consumed by growth_predictor.py) ────────────────────
    pf.resonance_series,
    pf.latest_score                     AS predictor_latest_score,
    pf.avg_delta                        AS predictor_avg_delta,
    pf.data_points                      AS predictor_data_points,

    -- ── Source attribution ───────────────────────────────────────────────────
    td.src_youtube,
    td.src_discord,
    td.src_sheets,
    CONCAT_WS(' · ',
        CASE WHEN td.src_youtube = 1 THEN 'YouTube' END,
        CASE WHEN td.src_discord = 1 THEN 'Discord' END,
        CASE WHEN td.src_sheets  = 1 THEN 'Sheets'  END
    )  AS source_attribution

FROM trends_delta AS td

LEFT JOIN resonance_trend AS rt
    ON  rt.period = td.period
    AND rt.topic  = td.topic

LEFT JOIN topic_momentum AS tm
    ON  tm.topic = td.topic

LEFT JOIN predictor_feed AS pf
    ON  pf.topic = td.topic

-- Limit high-momentum topics to keep result set focused
WHERE tm.momentum_rank <= :top_n
   OR tm.momentum_rank IS NULL          -- include rows with no topic_momentum match

ORDER BY
    td.period DESC,                     -- most recent period first
    tm.momentum_rank ASC NULLS LAST,    -- hottest topics within each period
    td.total_views DESC;                -- break ties by raw views
