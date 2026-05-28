-- =============================================================================
-- coral/queries/underperformers.sql
-- CreatorPulse · Content Diagnosis Query
-- =============================================================================
-- Role: Answers "Why did my recent videos flop?"
--       Detects underperforming content by cross-referencing YouTube metrics,
--       Discord community silence, and Google Sheets retention — then enriches
--       each row with root-cause flags, channel-relative benchmarks, and a
--       structured diagnosis context ready for Claude's insight prompt.
--
-- Powers:  routes/analytics.py · ai/insight_engine.py · ai/detectors.py
--          routes/insights.py (/insights/underperformers)
-- Params:  :timeframe_days       — lookback window (7 | 30 | 90), default 30
--          :watch_pct_threshold  — retention floor for flagging, default 40
--          :engagement_threshold — engagement-ratio floor, default 0.02
--          :discord_floor        — minimum expected community messages, default 5
--          :top_n                — worst-N videos to return, default 10
-- =============================================================================

-- ---------------------------------------------------------------------------
-- SECTION 1 · Channel baseline CTEs
-- Compute the creator's own average performance over the same timeframe so
-- underperformance is always relative, not against a generic threshold.
-- (Feature 10: Comparative Performance Benchmarking)
-- ---------------------------------------------------------------------------

WITH

-- 1a. Channel-level averages across all videos in the window
channel_baseline AS (
    SELECT
        ROUND(AVG(v.views), 0)                          AS baseline_views,
        ROUND(AVG(v.watch_pct), 2)                      AS baseline_watch_pct,
        ROUND(
            AVG((v.likes + v.comments) * 1.0
                / GREATEST(v.views, 1)), 4
        )                                               AS baseline_engagement_ratio,
        ROUND(AVG(v.ctr), 4)                            AS baseline_ctr,
        -- Used to normalise Discord message counts
        ROUND(
            AVG(v.views) / GREATEST(
                (SELECT COUNT(*) FROM youtube.videos
                 WHERE published_at >= NOW() - INTERVAL ':timeframe_days' DAY), 1
            ), 0
        )                                               AS baseline_views_per_video,
        COUNT(*)                                        AS total_videos_in_window
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
),

-- 1b. Per-topic averages — used for topic-level benchmarking
topic_baseline AS (
    SELECT
        v.topic,
        ROUND(AVG(v.views), 0)                          AS topic_avg_views,
        ROUND(AVG(v.watch_pct), 2)                      AS topic_avg_watch_pct,
        ROUND(
            AVG((v.likes + v.comments) * 1.0
                / GREATEST(v.views, 1)), 4
        )                                               AS topic_avg_engagement_ratio,
        COUNT(*)                                        AS topic_video_count
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY v.topic
),

-- ---------------------------------------------------------------------------
-- SECTION 2 · Raw source CTEs (mock-mode compatible — swap body only)
-- ---------------------------------------------------------------------------

-- 2a. YouTube videos with base engagement metrics
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
        v.ctr,
        v.watch_pct,
        ROUND(
            (v.likes + v.comments) * 1.0 / GREATEST(v.views, 1),
            4
        )                                               AS engagement_ratio
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
),

-- 2b. Discord activity per topic keyword — measures community silence or burst
disc_activity AS (
    SELECT
        m.keyword                                       AS topic,
        COUNT(*)                                        AS msg_count,
        COUNT(DISTINCT m.author_id)                     AS unique_authors,
        SUM(m.reply_count)                              AS reply_chains,
        -- Rough sentiment proxy: low reply-to-message ratio suggests disinterest
        ROUND(
            SUM(m.reply_count) * 1.0 / GREATEST(COUNT(*), 1),
            2
        )                                               AS reply_ratio,
        -- Flag: near-zero activity after upload = community silence
        CASE
            WHEN COUNT(*) < :discord_floor THEN 1
            ELSE 0
        END                                             AS flag_community_silence
    FROM discord.messages AS m
    WHERE m.created_at >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY m.keyword
),

-- 2c. Google Sheets retention log — most accurate watch% source
sheets_engagement AS (
    SELECT
        e.video_id,
        e.video_title,
        ROUND(AVG(e.watch_pct), 2)                      AS avg_watch_pct,
        ROUND(
            AVG((e.likes + e.comments) * 1.0
                / GREATEST(e.views, 1)), 4
        )                                               AS avg_quality_engagement,
        -- Negative sentiment proxy: repeated low watch% across log entries
        -- (multiple users bailed early = structural pacing/hook problem)
        COUNT(*) FILTER (WHERE e.watch_pct < :watch_pct_threshold) AS low_retention_entries,
        COUNT(*)                                        AS total_log_entries
    FROM sheets.engagement_log AS e
    WHERE e.date >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY e.video_id, e.video_title
),

-- ---------------------------------------------------------------------------
-- SECTION 3 · Cross-Source Failure Detection JOIN (MOST IMPORTANT)
-- Merge YouTube + Discord + Sheets per video.
-- Every signal that could explain underperformance is attached here.
-- ---------------------------------------------------------------------------

diagnosis_raw AS (
    SELECT
        -- ── Identity ──────────────────────────────────────────────────────
        yt.video_id,
        yt.title,
        yt.topic,
        yt.tag,
        yt.published_at,

        -- ── YouTube performance signals ────────────────────────────────────
        yt.views,
        yt.likes,
        yt.comments,
        yt.ctr,
        yt.engagement_ratio,

        -- Prefer Sheets watch_pct (granular); fall back to YouTube
        COALESCE(se.avg_watch_pct, yt.watch_pct, 0.0)  AS watch_pct,

        -- Quality engagement (Sheets-enhanced when available)
        COALESCE(se.avg_quality_engagement, yt.engagement_ratio) AS quality_engagement_ratio,

        -- ── Discord community signals ──────────────────────────────────────
        COALESCE(da.msg_count, 0)                       AS discord_msg_count,
        COALESCE(da.unique_authors, 0)                  AS discord_unique_authors,
        COALESCE(da.reply_chains, 0)                    AS discord_reply_chains,
        COALESCE(da.reply_ratio, 0)                     AS discord_reply_ratio,
        COALESCE(da.flag_community_silence, 1)          AS flag_community_silence,

        -- ── Sheets retention signals ───────────────────────────────────────
        COALESCE(se.low_retention_entries, 0)           AS low_retention_log_entries,
        COALESCE(se.total_log_entries, 0)               AS total_log_entries,

        -- Retention consistency: fraction of log entries with low watch%
        CASE
            WHEN COALESCE(se.total_log_entries, 0) > 0
            THEN ROUND(
                se.low_retention_entries * 1.0
                / GREATEST(se.total_log_entries, 1), 2
            )
            ELSE NULL
        END                                             AS low_retention_fraction,

        -- ── Source attribution ─────────────────────────────────────────────
        CASE WHEN yt.video_id IS NOT NULL  THEN 1 ELSE 0 END AS src_youtube,
        CASE WHEN da.topic IS NOT NULL     THEN 1 ELSE 0 END AS src_discord,
        CASE WHEN se.video_id IS NOT NULL  THEN 1 ELSE 0 END AS src_sheets

    FROM yt_videos AS yt
    LEFT JOIN disc_activity AS da
        ON  yt.topic ILIKE '%' || da.topic || '%'
    LEFT JOIN sheets_engagement AS se
        ON  se.video_id = yt.video_id
         OR se.video_title ILIKE yt.title
),

-- ---------------------------------------------------------------------------
-- SECTION 4 · Root-Cause Flag Computation
-- Each flag maps to a concrete diagnosis category that insight_engine.py
-- will pass to Claude as structured context.
-- ---------------------------------------------------------------------------

diagnosis_flagged AS (
    SELECT
        dr.*,

        -- Bring in channel + topic baselines for relative scoring
        cb.baseline_views,
        cb.baseline_watch_pct,
        cb.baseline_engagement_ratio,
        cb.baseline_ctr,
        tb.topic_avg_views,
        tb.topic_avg_watch_pct,
        tb.topic_avg_engagement_ratio,

        -- ── Flag 1: Low Retention (Feature 3) ─────────────────────────────
        -- Watch% below the user-configurable threshold
        CASE
            WHEN dr.watch_pct < :watch_pct_threshold THEN 1
            ELSE 0
        END                                             AS flag_low_retention,

        -- ── Flag 2: Weak Engagement (Feature 4) ───────────────────────────
        CASE
            WHEN dr.quality_engagement_ratio < :engagement_threshold THEN 1
            ELSE 0
        END                                             AS flag_weak_engagement,

        -- ── Flag 3: Low Community Resonance / Discord Silence (Feature 5) ─
        -- Already computed in disc_activity CTE, carried through here
        dr.flag_community_silence                       AS flag_discord_silence,

        -- ── Flag 4: False Popularity (Feature 7) ──────────────────────────
        -- High views but weak retention AND weak community
        CASE
            WHEN dr.views > cb.baseline_views * 1.5     -- above-average views
             AND dr.watch_pct < :watch_pct_threshold     -- but low retention
             AND dr.discord_msg_count < :discord_floor   -- and quiet community
            THEN 1
            ELSE 0
        END                                             AS flag_false_popularity,

        -- ── Flag 5: High CTR, Low Retention (thumbnail/title mismatch) ───
        -- Strong click-through but audience leaves early = misleading title
        CASE
            WHEN dr.ctr > cb.baseline_ctr * 1.2
             AND dr.watch_pct < :watch_pct_threshold
            THEN 1
            ELSE 0
        END                                             AS flag_ctr_retention_mismatch,

        -- ── Flag 6: Below-Baseline Views (Feature 10 — relative) ──────────
        CASE
            WHEN dr.views < cb.baseline_views * 0.5 THEN 1
            ELSE 0
        END                                             AS flag_below_baseline_views,

        -- ── Flag 7: Below-Topic-Average (Feature 10 — topic-relative) ─────
        CASE
            WHEN dr.views < tb.topic_avg_views * 0.6 THEN 1
            ELSE 0
        END                                             AS flag_below_topic_avg,

        -- ── Flag 8: Retention Consistency Issue (Feature 9 — sentiment) ───
        -- Multiple log entries showing sub-threshold watch% = structural problem
        CASE
            WHEN COALESCE(
                dr.low_retention_fraction, 0
            ) > 0.6 THEN 1
            ELSE 0
        END                                             AS flag_retention_consistency,

        -- ── Gap vs channel baseline (% deviation) ─────────────────────────
        ROUND(
            (dr.views - cb.baseline_views) * 100.0
            / GREATEST(cb.baseline_views, 1),
            1
        )                                               AS views_vs_baseline_pct,

        ROUND(
            (dr.watch_pct - cb.baseline_watch_pct) * 100.0
            / GREATEST(cb.baseline_watch_pct, 1),
            1
        )                                               AS watch_pct_vs_baseline_pct,

        ROUND(
            (dr.quality_engagement_ratio - cb.baseline_engagement_ratio) * 100.0
            / GREATEST(cb.baseline_engagement_ratio, 0.0001),
            1
        )                                               AS engagement_vs_baseline_pct

    FROM diagnosis_raw AS dr
    CROSS JOIN channel_baseline AS cb
    LEFT JOIN topic_baseline AS tb
        ON tb.topic = dr.topic
),

-- ---------------------------------------------------------------------------
-- SECTION 5 · Underperformance Score & Ranking
-- Composite score where each flag adds weight; used to rank worst videos.
-- Higher score = more severe underperformance.
-- ---------------------------------------------------------------------------

diagnosis_scored AS (
    SELECT
        df.*,

        -- Underperformance severity score (0–100 scale)
        ROUND(
              df.flag_low_retention            * 20.0
            + df.flag_weak_engagement          * 15.0
            + df.flag_discord_silence          * 20.0
            + df.flag_false_popularity         * 15.0
            + df.flag_ctr_retention_mismatch   * 10.0
            + df.flag_below_baseline_views     * 10.0
            + df.flag_below_topic_avg          *  5.0
            + df.flag_retention_consistency    *  5.0,
            1
        )                                               AS underperformance_score,

        -- Human-readable primary diagnosis (top contributing failure mode)
        CASE
            WHEN df.flag_false_popularity = 1
            THEN 'false_popularity'
            WHEN df.flag_ctr_retention_mismatch = 1
            THEN 'title_thumbnail_mismatch'
            WHEN df.flag_low_retention = 1 AND df.flag_discord_silence = 1
            THEN 'low_retention_and_community_silence'
            WHEN df.flag_low_retention = 1
            THEN 'low_retention'
            WHEN df.flag_discord_silence = 1
            THEN 'community_silence'
            WHEN df.flag_weak_engagement = 1
            THEN 'weak_engagement'
            WHEN df.flag_below_baseline_views = 1
            THEN 'below_average_reach'
            ELSE 'marginal_underperformance'
        END                                             AS primary_diagnosis,

        -- Structured diagnosis list for Claude (pipe-delimited for easy split)
        CONCAT_WS(' | ',
            CASE WHEN df.flag_low_retention          = 1 THEN 'low_retention'           END,
            CASE WHEN df.flag_weak_engagement        = 1 THEN 'weak_engagement'         END,
            CASE WHEN df.flag_discord_silence        = 1 THEN 'community_silence'       END,
            CASE WHEN df.flag_false_popularity       = 1 THEN 'false_popularity'        END,
            CASE WHEN df.flag_ctr_retention_mismatch = 1 THEN 'ctr_retention_mismatch'  END,
            CASE WHEN df.flag_below_baseline_views   = 1 THEN 'below_baseline_views'    END,
            CASE WHEN df.flag_below_topic_avg        = 1 THEN 'below_topic_avg'         END,
            CASE WHEN df.flag_retention_consistency  = 1 THEN 'retention_consistency'   END
        )                                               AS diagnosis_flags_list,

        -- Rank: 1 = worst underperformer
        ROW_NUMBER() OVER (
            ORDER BY (
                  df.flag_low_retention            * 20.0
                + df.flag_weak_engagement          * 15.0
                + df.flag_discord_silence          * 20.0
                + df.flag_false_popularity         * 15.0
                + df.flag_ctr_retention_mismatch   * 10.0
                + df.flag_below_baseline_views     * 10.0
                + df.flag_below_topic_avg          *  5.0
                + df.flag_retention_consistency    *  5.0
            ) DESC
        )                                               AS underperformance_rank

    FROM diagnosis_flagged AS df
    -- Only surface videos with at least one active failure flag
    WHERE (
          df.flag_low_retention
        + df.flag_weak_engagement
        + df.flag_discord_silence
        + df.flag_false_popularity
        + df.flag_ctr_retention_mismatch
        + df.flag_below_baseline_views
        + df.flag_below_topic_avg
        + df.flag_retention_consistency
    ) > 0
),

-- ---------------------------------------------------------------------------
-- SECTION 6 · Topic-Level Underperformance Aggregation (Feature 6)
-- Rolls up individual video failures to topic level so Claude can answer
-- "What content should I stop making?" with data-backed specifics.
-- ---------------------------------------------------------------------------

topic_underperformance AS (
    SELECT
        ds.topic,
        COUNT(*)                                        AS weak_video_count,
        ROUND(AVG(ds.underperformance_score), 1)        AS avg_underperformance_score,
        ROUND(AVG(ds.watch_pct), 2)                     AS topic_avg_watch_pct,
        ROUND(AVG(ds.discord_msg_count), 1)             AS topic_avg_discord_msgs,
        ROUND(AVG(ds.views_vs_baseline_pct), 1)         AS topic_avg_views_vs_baseline_pct,
        -- Most common failure mode for this topic
        MODE() WITHIN GROUP (
            ORDER BY ds.primary_diagnosis
        )                                               AS most_common_diagnosis,
        ROW_NUMBER() OVER (
            ORDER BY AVG(ds.underperformance_score) DESC
        )                                               AS topic_weak_rank
    FROM diagnosis_scored AS ds
    GROUP BY ds.topic
),

-- ---------------------------------------------------------------------------
-- SECTION 7 · Recovery Recommendation Context (Feature 14)
-- Pre-computes recovery signals so recommendations.py can generate
-- structured Recommendation objects without a second query.
-- ---------------------------------------------------------------------------

recovery_context AS (
    SELECT
        ds.topic,
        -- Best-performing topic in the same window (comparison anchor)
        FIRST_VALUE(ds.topic) OVER (
            ORDER BY ds.underperformance_score ASC
        )                                               AS best_performing_topic,
        -- Median watch% of non-underperforming videos (recovery target)
        PERCENTILE_CONT(0.5) WITHIN GROUP (
            ORDER BY ds.watch_pct
        ) OVER ()                                       AS median_channel_watch_pct,
        -- If the creator's AI content outperforms career content — actionable
        MAX(CASE
            WHEN ds.topic ILIKE '%AI%' OR ds.topic ILIKE '%agent%'
            THEN ds.watch_pct
        END) OVER ()                                    AS best_ai_watch_pct,
        MAX(CASE
            WHEN ds.topic ILIKE '%career%' OR ds.topic ILIKE '%productivity%'
            THEN ds.watch_pct
        END) OVER ()                                    AS lifestyle_watch_pct
    FROM diagnosis_scored AS ds
)

-- =============================================================================
-- FINAL SELECT — Claude-ready + Dashboard-ready underperformer output
-- =============================================================================
-- Returns one row per underperforming video, ranked worst-first, with:
--   · All root-cause flags and primary diagnosis label
--   · Relative deviation from channel and topic baselines
--   · Discord community silence signals
--   · Structured diagnosis_flags_list for insight_engine.py prompt injection
--   · Topic-level aggregates for "stop making this content" recommendations
--   · Recovery context for recommendations.py
--   · Source attribution for frontend badges
-- Limited to :top_n worst videos for fast response and clean AI context.
-- =============================================================================

SELECT
    -- ── Identity ─────────────────────────────────────────────────────────────
    ds.video_id,
    ds.title,
    ds.topic,
    ds.tag,
    ds.published_at,
    ds.underperformance_rank,
    ds.underperformance_score,

    -- ── Core performance signals ─────────────────────────────────────────────
    ds.views,
    ds.watch_pct,
    ds.ctr,
    ds.engagement_ratio,
    ds.quality_engagement_ratio,
    ds.discord_msg_count,
    ds.discord_unique_authors,
    ds.discord_reply_ratio,

    -- ── Channel-relative deviation ────────────────────────────────────────────
    ds.baseline_views,
    ds.baseline_watch_pct,
    ds.baseline_engagement_ratio,
    ds.views_vs_baseline_pct,
    ds.watch_pct_vs_baseline_pct,
    ds.engagement_vs_baseline_pct,

    -- ── Topic-relative deviation ──────────────────────────────────────────────
    ds.topic_avg_views,
    ds.topic_avg_watch_pct,
    ds.topic_avg_engagement_ratio,

    -- ── Root-cause flags (individual) ────────────────────────────────────────
    ds.flag_low_retention,
    ds.flag_weak_engagement,
    ds.flag_discord_silence,
    ds.flag_false_popularity,
    ds.flag_ctr_retention_mismatch,
    ds.flag_below_baseline_views,
    ds.flag_below_topic_avg,
    ds.flag_retention_consistency,
    ds.low_retention_fraction,

    -- ── Structured diagnosis (for Claude prompt injection) ────────────────────
    ds.primary_diagnosis,
    ds.diagnosis_flags_list,

    -- ── Topic-level underperformance aggregates ───────────────────────────────
    tu.weak_video_count                 AS topic_weak_video_count,
    tu.avg_underperformance_score       AS topic_avg_underperformance,
    tu.topic_avg_watch_pct              AS topic_underperformer_avg_watch_pct,
    tu.most_common_diagnosis            AS topic_most_common_diagnosis,
    tu.topic_weak_rank,

    -- ── Recovery context (consumed by recommendations.py) ────────────────────
    rc.median_channel_watch_pct         AS recovery_target_watch_pct,
    rc.best_ai_watch_pct                AS best_ai_topic_watch_pct,
    rc.lifestyle_watch_pct              AS lifestyle_topic_watch_pct,
    ROUND(
        COALESCE(rc.best_ai_watch_pct, 0)
        - COALESCE(rc.lifestyle_watch_pct, 0),
        1
    )                                   AS ai_vs_lifestyle_watch_pct_gap,

    -- ── Source attribution ────────────────────────────────────────────────────
    ds.src_youtube,
    ds.src_discord,
    ds.src_sheets,
    CONCAT_WS(' · ',
        CASE WHEN ds.src_youtube = 1 THEN 'YouTube' END,
        CASE WHEN ds.src_discord = 1 THEN 'Discord' END,
        CASE WHEN ds.src_sheets  = 1 THEN 'Sheets'  END
    )                                   AS source_attribution

FROM diagnosis_scored AS ds

LEFT JOIN topic_underperformance AS tu
    ON  tu.topic = ds.topic

LEFT JOIN recovery_context AS rc
    ON  rc.topic = ds.topic

WHERE ds.underperformance_rank <= :top_n

ORDER BY ds.underperformance_rank ASC;
