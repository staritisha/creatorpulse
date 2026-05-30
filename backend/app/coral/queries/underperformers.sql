-- =============================================================================
-- coral/queries/underperformers.sql
-- CreatorPulse · Content Diagnosis Query
-- =============================================================================
-- Role: Answers "Why did my recent videos flop?"
--       Detects underperforming content by cross-referencing YouTube metrics,
--       Discord community silence, and Google Sheets engagement — then
--       enriches each row with root-cause flags, channel-relative benchmarks,
--       and a structured diagnosis context ready for Claude's insight prompt.
--
-- Powers:  routes/analytics.py · ai/insight_engine.py · ai/detectors.py
-- Params:  :timeframe_days       — lookback window (7 | 30 | 90), default 30
--          :watch_pct_threshold  — retention floor for flagging, default 40
--          :engagement_threshold — engagement-ratio floor, default 0.02
--          :discord_floor        — minimum expected community messages, default 5
--          :top_n                — worst-N videos to return, default 10
--
-- Schema (actual JSONL columns):
--   youtube.videos       → video_id, title, published_at, topic, views,
--                          watch_pct, likes, comments, ctr_percent,
--                          resonance_score, avg_view_duration_sec
--   discord.messages     → message_id, video_ref, author, channel,
--                          timestamp, sentiment, reply_count, total_reactions
--   gsheets.engagement_log → date, video_id, cta_clicks, link_clicks,
--                          email_signups, poll_responses, notes
-- =============================================================================

WITH

-- ---------------------------------------------------------------------------
-- 1a. Channel-level baseline (all videos in the window)
-- ---------------------------------------------------------------------------
channel_baseline AS (
    SELECT
        ROUND(AVG(v.views), 0)              AS baseline_views,
        ROUND(AVG(v.watch_pct), 2)          AS baseline_watch_pct,
        ROUND(
            AVG((v.likes + v.comments) * 1.0 / GREATEST(v.views, 1)),
            4
        )                                   AS baseline_engagement_ratio,
        ROUND(AVG(v.ctr_percent), 4)        AS baseline_ctr_percent,
        COUNT(v.video_id)                   AS total_videos_in_window
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
),

-- ---------------------------------------------------------------------------
-- 1b. Per-topic baseline
-- ---------------------------------------------------------------------------
topic_baseline AS (
    SELECT
        v.topic,
        ROUND(AVG(v.views), 0)              AS topic_avg_views,
        ROUND(AVG(v.watch_pct), 2)          AS topic_avg_watch_pct,
        ROUND(
            AVG((v.likes + v.comments) * 1.0 / GREATEST(v.views, 1)),
            4
        )                                   AS topic_avg_engagement_ratio,
        COUNT(v.video_id)                   AS topic_video_count
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY v.topic
),

-- ---------------------------------------------------------------------------
-- 2a. YouTube videos with base engagement metrics
-- ---------------------------------------------------------------------------
yt_videos AS (
    SELECT
        v.video_id,
        v.title,
        v.topic,
        v.published_at,
        v.views,
        v.likes,
        v.comments,
        v.ctr_percent,
        v.watch_pct,
        v.resonance_score                       AS precomputed_resonance,
        ROUND(
            (v.likes + v.comments) * 1.0 / GREATEST(v.views, 1),
            4
        )                                       AS engagement_ratio
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
),

-- ---------------------------------------------------------------------------
-- 2b. Discord activity per video (joined by video_ref)
-- ---------------------------------------------------------------------------
disc_agg AS (
    SELECT
        m.video_ref                             AS video_id,
        COUNT(m.message_id)                     AS msg_count,
        COUNT(DISTINCT m.author)                AS unique_authors,
        SUM(m.reply_count)                      AS reply_chains,
        SUM(m.total_reactions)                  AS total_reactions,
        ROUND(
            SUM(m.reply_count) * 1.0 / GREATEST(COUNT(m.message_id), 1),
            2
        )                                       AS reply_ratio,
        -- Community silence flag: fewer messages than the configured floor
        CASE
            WHEN COUNT(m.message_id) < :discord_floor THEN 1
            ELSE 0
        END                                     AS flag_community_silence,
        -- Negative sentiment indicator
        ROUND(
            SUM(CASE WHEN m.sentiment = 'negative' THEN 1.0 ELSE 0.0 END)
            / NULLIF(COUNT(m.message_id), 0),
            3
        )                                       AS negative_sentiment_ratio
    FROM discord.messages AS m
    WHERE m.video_ref IS NOT NULL
      AND m.video_ref != ''
    GROUP BY m.video_ref
),

-- ---------------------------------------------------------------------------
-- 2c. Google Sheets engagement per video (SUM across date rows)
-- ---------------------------------------------------------------------------
sheets_agg AS (
    SELECT
        s.video_id,
        SUM(s.cta_clicks)           AS total_cta_clicks,
        SUM(s.link_clicks)          AS total_link_clicks,
        SUM(s.email_signups)        AS total_email_signups,
        SUM(s.poll_responses)       AS total_poll_responses,
        COUNT(s.date)               AS log_entry_count,
        MAX(s.notes)                AS latest_notes
    FROM gsheets.engagement_log AS s
    WHERE s.date >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY s.video_id
),

-- ---------------------------------------------------------------------------
-- 3. Cross-Source Diagnosis JOIN (most important — 3 sources in one query)
-- ---------------------------------------------------------------------------
diagnosis_raw AS (
    SELECT
        -- Identity
        yt.video_id,
        yt.title,
        yt.topic,
        yt.published_at,

        -- YouTube signals
        yt.views,
        yt.likes,
        yt.comments,
        yt.ctr_percent,
        yt.watch_pct,
        yt.engagement_ratio,
        yt.precomputed_resonance,

        -- Discord community signals
        COALESCE(da.msg_count,               0)     AS discord_msg_count,
        COALESCE(da.unique_authors,          0)     AS discord_unique_authors,
        COALESCE(da.reply_chains,            0)     AS discord_reply_chains,
        COALESCE(da.total_reactions,         0)     AS discord_total_reactions,
        COALESCE(da.reply_ratio,             0.0)   AS discord_reply_ratio,
        COALESCE(da.flag_community_silence,  1)     AS flag_community_silence,
        COALESCE(da.negative_sentiment_ratio, 0.0)  AS negative_sentiment_ratio,

        -- Sheets conversion signals
        COALESCE(sa.total_cta_clicks,        0)     AS cta_clicks,
        COALESCE(sa.total_email_signups,     0)     AS email_signups,
        COALESCE(sa.log_entry_count,         0)     AS sheets_log_entries,
        COALESCE(sa.latest_notes,           '')     AS creator_notes,

        -- Source attribution
        1                                           AS src_youtube,
        CASE WHEN da.video_id IS NOT NULL THEN 1 ELSE 0 END AS src_discord,
        CASE WHEN sa.video_id IS NOT NULL THEN 1 ELSE 0 END AS src_sheets

    FROM yt_videos AS yt
    LEFT JOIN disc_agg   AS da ON da.video_id = yt.video_id
    LEFT JOIN sheets_agg AS sa ON sa.video_id = yt.video_id
),

-- ---------------------------------------------------------------------------
-- 4. Root-Cause Flags + Baseline Comparison
-- ---------------------------------------------------------------------------
diagnosis_flagged AS (
    SELECT
        dr.*,

        -- Channel baseline
        cb.baseline_views,
        cb.baseline_watch_pct,
        cb.baseline_engagement_ratio,
        cb.baseline_ctr_percent,

        -- Topic baseline
        tb.topic_avg_views,
        tb.topic_avg_watch_pct,
        tb.topic_avg_engagement_ratio,

        -- Flag 1: Low Retention
        CASE WHEN dr.watch_pct < :watch_pct_threshold        THEN 1 ELSE 0 END
            AS flag_low_retention,

        -- Flag 2: Weak Engagement
        CASE WHEN dr.engagement_ratio < :engagement_threshold THEN 1 ELSE 0 END
            AS flag_weak_engagement,

        -- Flag 3: Community Silence (already computed in disc_agg)
        dr.flag_community_silence                            AS flag_discord_silence,

        -- Flag 4: False Popularity (high views, low retention AND quiet community)
        CASE
            WHEN dr.views > cb.baseline_views * 1.5
             AND dr.watch_pct < :watch_pct_threshold
             AND dr.discord_msg_count < :discord_floor
            THEN 1 ELSE 0
        END                                                  AS flag_false_popularity,

        -- Flag 5: CTR/Retention Mismatch (thumbnail gets clicks, content loses viewers)
        CASE
            WHEN dr.ctr_percent > cb.baseline_ctr_percent * 1.2
             AND dr.watch_pct   < :watch_pct_threshold
            THEN 1 ELSE 0
        END                                                  AS flag_ctr_retention_mismatch,

        -- Flag 6: Below-Baseline Views
        CASE WHEN dr.views < cb.baseline_views * 0.5         THEN 1 ELSE 0 END
            AS flag_below_baseline_views,

        -- Flag 7: Below-Topic-Average
        CASE WHEN dr.views < tb.topic_avg_views * 0.6        THEN 1 ELSE 0 END
            AS flag_below_topic_avg,

        -- Flag 8: Negative Community Sentiment
        CASE WHEN dr.negative_sentiment_ratio > 0.4          THEN 1 ELSE 0 END
            AS flag_negative_sentiment,

        -- Relative deviation from channel baseline (%)
        ROUND(
            (dr.views - cb.baseline_views) * 100.0
            / GREATEST(cb.baseline_views, 1), 1
        )                                                    AS views_vs_baseline_pct,
        ROUND(
            (dr.watch_pct - cb.baseline_watch_pct) * 100.0
            / GREATEST(cb.baseline_watch_pct, 1), 1
        )                                                    AS watch_pct_vs_baseline_pct,
        ROUND(
            (dr.engagement_ratio - cb.baseline_engagement_ratio) * 100.0
            / GREATEST(cb.baseline_engagement_ratio, 0.0001), 1
        )                                                    AS engagement_vs_baseline_pct

    FROM diagnosis_raw AS dr
    CROSS JOIN channel_baseline AS cb
    LEFT JOIN topic_baseline AS tb ON tb.topic = dr.topic
),

-- ---------------------------------------------------------------------------
-- 5. Underperformance Score + Primary Diagnosis
-- ---------------------------------------------------------------------------
diagnosis_scored AS (
    SELECT
        df.*,

        -- Severity score (0–100 scale, higher = worse)
        ROUND(
              df.flag_low_retention          * 20.0
            + df.flag_weak_engagement        * 15.0
            + df.flag_discord_silence        * 20.0
            + df.flag_false_popularity       * 15.0
            + df.flag_ctr_retention_mismatch * 10.0
            + df.flag_below_baseline_views   * 10.0
            + df.flag_below_topic_avg        *  5.0
            + df.flag_negative_sentiment     *  5.0,
            1
        )                                                    AS underperformance_score,

        -- Human-readable primary diagnosis
        CASE
            WHEN df.flag_false_popularity       = 1 THEN 'false_popularity'
            WHEN df.flag_ctr_retention_mismatch = 1 THEN 'title_thumbnail_mismatch'
            WHEN df.flag_low_retention = 1 AND df.flag_discord_silence = 1
                THEN 'low_retention_and_community_silence'
            WHEN df.flag_low_retention          = 1 THEN 'low_retention'
            WHEN df.flag_discord_silence        = 1 THEN 'community_silence'
            WHEN df.flag_weak_engagement        = 1 THEN 'weak_engagement'
            WHEN df.flag_below_baseline_views   = 1 THEN 'below_average_reach'
            WHEN df.flag_negative_sentiment     = 1 THEN 'negative_community_reaction'
            ELSE 'marginal_underperformance'
        END                                                  AS primary_diagnosis,

        -- Pipe-delimited diagnosis list (for Claude prompt injection)
        CONCAT_WS(' | ',
            CASE WHEN df.flag_low_retention          = 1 THEN 'low_retention'            END,
            CASE WHEN df.flag_weak_engagement        = 1 THEN 'weak_engagement'          END,
            CASE WHEN df.flag_discord_silence        = 1 THEN 'community_silence'        END,
            CASE WHEN df.flag_false_popularity       = 1 THEN 'false_popularity'         END,
            CASE WHEN df.flag_ctr_retention_mismatch = 1 THEN 'ctr_retention_mismatch'   END,
            CASE WHEN df.flag_below_baseline_views   = 1 THEN 'below_baseline_views'     END,
            CASE WHEN df.flag_below_topic_avg        = 1 THEN 'below_topic_avg'          END,
            CASE WHEN df.flag_negative_sentiment     = 1 THEN 'negative_sentiment'       END
        )                                                    AS diagnosis_flags_list,

        -- Rank: 1 = worst underperformer
        ROW_NUMBER() OVER (
            ORDER BY (
                  df.flag_low_retention          * 20.0
                + df.flag_weak_engagement        * 15.0
                + df.flag_discord_silence        * 20.0
                + df.flag_false_popularity       * 15.0
                + df.flag_ctr_retention_mismatch * 10.0
                + df.flag_below_baseline_views   * 10.0
                + df.flag_below_topic_avg        *  5.0
                + df.flag_negative_sentiment     *  5.0
            ) DESC
        )                                                    AS underperformance_rank

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
        + df.flag_negative_sentiment
    ) > 0
),

-- ---------------------------------------------------------------------------
-- 6. Topic-Level Underperformance Aggregation
-- ---------------------------------------------------------------------------
topic_underperformance AS (
    SELECT
        ds.topic,
        COUNT(ds.video_id)                          AS weak_video_count,
        ROUND(AVG(ds.underperformance_score), 1)    AS avg_underperformance_score,
        ROUND(AVG(ds.watch_pct), 2)                 AS topic_avg_watch_pct,
        ROUND(AVG(ds.discord_msg_count), 1)         AS topic_avg_discord_msgs,
        ROUND(AVG(ds.views_vs_baseline_pct), 1)     AS topic_avg_views_vs_baseline_pct,
        ROW_NUMBER() OVER (
            ORDER BY AVG(ds.underperformance_score) DESC
        )                                           AS topic_weak_rank
    FROM diagnosis_scored AS ds
    GROUP BY ds.topic
)

-- =============================================================================
-- FINAL SELECT — one row per underperforming video, Claude-ready
-- =============================================================================
SELECT
    -- Identity
    ds.video_id,
    ds.title,
    ds.topic,
    ds.published_at,
    ds.underperformance_rank,
    ds.underperformance_score,
    ds.primary_diagnosis,
    ds.diagnosis_flags_list,

    -- Core performance signals
    ds.views,
    ds.watch_pct,
    ds.ctr_percent,
    ds.engagement_ratio,
    ds.discord_msg_count,
    ds.discord_unique_authors,
    ds.discord_reply_ratio,
    ds.negative_sentiment_ratio,
    ds.cta_clicks,
    ds.email_signups,
    ds.creator_notes,

    -- Channel-relative deviation
    ds.baseline_views,
    ds.baseline_watch_pct,
    ds.baseline_engagement_ratio,
    ds.views_vs_baseline_pct,
    ds.watch_pct_vs_baseline_pct,
    ds.engagement_vs_baseline_pct,

    -- Topic-relative deviation
    ds.topic_avg_views,
    ds.topic_avg_watch_pct,
    ds.topic_avg_engagement_ratio,

    -- Individual root-cause flags
    ds.flag_low_retention,
    ds.flag_weak_engagement,
    ds.flag_discord_silence,
    ds.flag_false_popularity,
    ds.flag_ctr_retention_mismatch,
    ds.flag_below_baseline_views,
    ds.flag_below_topic_avg,
    ds.flag_negative_sentiment,

    -- Topic-level aggregates
    tu.weak_video_count             AS topic_weak_video_count,
    tu.avg_underperformance_score   AS topic_avg_underperformance,
    tu.topic_avg_watch_pct          AS topic_underperformer_avg_watch_pct,
    tu.topic_weak_rank,

    -- Source attribution
    ds.src_youtube,
    ds.src_discord,
    ds.src_sheets,
    CONCAT_WS(' · ',
        CASE WHEN ds.src_youtube = 1 THEN 'YouTube' END,
        CASE WHEN ds.src_discord = 1 THEN 'Discord' END,
        CASE WHEN ds.src_sheets  = 1 THEN 'Sheets'  END
    )                               AS source_attribution

FROM diagnosis_scored AS ds

LEFT JOIN topic_underperformance AS tu
    ON tu.topic = ds.topic

WHERE ds.underperformance_rank <= :top_n

ORDER BY ds.underperformance_rank ASC;
