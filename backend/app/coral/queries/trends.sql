-- =============================================================================
-- coral/queries/trends.sql
-- CreatorPulse · Creator Evolution Intelligence Query
-- =============================================================================
-- Role: Answers "What is CHANGING over time?"
--       Tracks growth momentum, topic evolution, community acceleration,
--       and retention improvement across configurable time windows.
--
-- Powers:  routes/analytics.py · ai/insight_engine.py · scoring/growth_predictor.py
-- Params:  :timeframe_days  — total lookback window (7 | 30 | 90), default 90
--          :bucket          — time grouping ('day' | 'week' | 'month'), default 'week'
--          :topic_filter    — optional topic string, NULL = all
--          :top_n           — max topic rows, default 10
--
-- Schema (actual JSONL columns):
--   youtube.videos       → video_id, title, published_at, topic, views,
--                          watch_pct, likes, comments, ctr_percent,
--                          resonance_score, watch_time_minutes, shares
--   discord.messages     → message_id, video_ref, author, channel,
--                          timestamp, sentiment, reply_count, total_reactions
--   gsheets.engagement_log → date, video_id, cta_clicks, link_clicks,
--                          email_signups, poll_responses, notes
-- =============================================================================

WITH

-- ---------------------------------------------------------------------------
-- 1a. YouTube videos bucketed by publish period
-- ---------------------------------------------------------------------------
yt_by_period AS (
    SELECT
        DATE_TRUNC(:bucket, v.published_at)     AS period,
        v.topic,
        COUNT(v.video_id)                       AS videos_published,
        SUM(v.views)                            AS total_views,
        SUM(v.likes)                            AS total_likes,
        SUM(v.comments)                         AS total_comments,
        ROUND(AVG(v.watch_pct), 2)              AS avg_watch_pct,
        ROUND(
            SUM(v.likes + v.comments) * 1.0
            / GREATEST(SUM(v.views), 1),
            4
        )                                       AS period_engagement_ratio,
        ROUND(AVG(v.ctr_percent), 4)            AS avg_ctr_percent
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
      AND (:topic_filter IS NULL OR v.topic ILIKE '%' || :topic_filter || '%')
    GROUP BY DATE_TRUNC(:bucket, v.published_at), v.topic
),

-- ---------------------------------------------------------------------------
-- 1b. Discord messages bucketed by message timestamp
--     Aggregated per period + video_ref, then joined to YouTube via video_ref
--     (no keyword/topic column exists in the JSONL — join through video)
-- ---------------------------------------------------------------------------
disc_by_period AS (
    SELECT
        DATE_TRUNC(:bucket, m.timestamp)        AS period,
        m.video_ref,
        COUNT(m.message_id)                     AS total_messages,
        COUNT(DISTINCT m.author)                AS active_members,
        SUM(m.reply_count)                      AS total_replies,
        SUM(m.total_reactions)                  AS total_reactions,
        -- Positive sentiment ratio for the period
        ROUND(
            SUM(CASE WHEN m.sentiment = 'positive' THEN 1.0 ELSE 0.0 END)
            / NULLIF(COUNT(m.message_id), 0),
            3
        )                                       AS positive_sentiment_ratio
    FROM discord.messages AS m
    WHERE m.timestamp >= NOW() - INTERVAL ':timeframe_days' DAY
      AND m.video_ref IS NOT NULL
      AND m.video_ref != ''
    GROUP BY DATE_TRUNC(:bucket, m.timestamp), m.video_ref
),

-- ---------------------------------------------------------------------------
-- 1b-join helper: bridge discord periods to topics via youtube.videos
-- ---------------------------------------------------------------------------
disc_topic_bridge AS (
    SELECT
        dp.period,
        v.topic,
        SUM(dp.total_messages)                  AS total_messages,
        SUM(dp.active_members)                  AS active_members,
        SUM(dp.total_replies)                   AS total_replies,
        SUM(dp.total_reactions)                 AS total_reactions,
        ROUND(AVG(dp.positive_sentiment_ratio), 3) AS positive_sentiment_ratio
    FROM disc_by_period AS dp
    JOIN youtube.videos AS v ON v.video_id = dp.video_ref
    GROUP BY dp.period, v.topic
),

-- ---------------------------------------------------------------------------
-- 1c. Google Sheets engagement bucketed by date
-- ---------------------------------------------------------------------------
sheets_by_period AS (
    SELECT
        DATE_TRUNC(:bucket, s.date)             AS period,
        s.video_id,
        SUM(s.cta_clicks)                       AS total_cta_clicks,
        SUM(s.email_signups)                    AS total_email_signups,
        SUM(s.poll_responses)                   AS total_poll_responses
    FROM gsheets.engagement_log AS s
    WHERE s.date >= NOW() - INTERVAL ':timeframe_days' DAY
    GROUP BY DATE_TRUNC(:bucket, s.date), s.video_id
),

-- sheets aggregated to topic level via youtube join
sheets_topic_bridge AS (
    SELECT
        sp.period,
        v.topic,
        SUM(sp.total_cta_clicks)                AS total_cta_clicks,
        SUM(sp.total_email_signups)             AS total_email_signups,
        SUM(sp.total_poll_responses)            AS total_poll_responses
    FROM sheets_by_period AS sp
    JOIN youtube.videos AS v ON v.video_id = sp.video_id
    GROUP BY sp.period, v.topic
),

-- ---------------------------------------------------------------------------
-- 2. Cross-Source Trend JOIN on (period, topic)
-- ---------------------------------------------------------------------------
trends_raw AS (
    SELECT
        yt.period,
        yt.topic,

        -- YouTube signals
        yt.videos_published,
        yt.total_views,
        yt.total_likes,
        yt.total_comments,
        yt.avg_watch_pct,
        yt.period_engagement_ratio,
        yt.avg_ctr_percent,

        -- Discord signals
        COALESCE(da.total_messages,          0)     AS discord_messages,
        COALESCE(da.active_members,          0)     AS discord_active_members,
        COALESCE(da.total_replies,           0)     AS discord_replies,
        COALESCE(da.total_reactions,         0)     AS discord_reactions,
        COALESCE(da.positive_sentiment_ratio, 0.0)  AS discord_sentiment_ratio,

        -- Sheets signals
        COALESCE(sb.total_cta_clicks,        0)     AS cta_clicks,
        COALESCE(sb.total_email_signups,     0)     AS email_signups,
        COALESCE(sb.total_poll_responses,    0)     AS poll_responses,

        -- Source attribution
        1                                           AS src_youtube,
        CASE WHEN da.topic IS NOT NULL THEN 1 ELSE 0 END AS src_discord,
        CASE WHEN sb.topic IS NOT NULL THEN 1 ELSE 0 END AS src_sheets

    FROM yt_by_period AS yt
    LEFT JOIN disc_topic_bridge   AS da ON da.period = yt.period AND da.topic = yt.topic
    LEFT JOIN sheets_topic_bridge AS sb ON sb.period = yt.period AND sb.topic = yt.topic
),

-- ---------------------------------------------------------------------------
-- 3. Period-over-Period Change (LAG window functions)
-- ---------------------------------------------------------------------------
trends_delta AS (
    SELECT
        t.*,

        -- Views delta
        t.total_views
            - LAG(t.total_views) OVER (PARTITION BY t.topic ORDER BY t.period)
                                                    AS views_delta,
        ROUND(
            (t.total_views
             - LAG(t.total_views) OVER (PARTITION BY t.topic ORDER BY t.period))
            * 100.0
            / GREATEST(LAG(t.total_views) OVER (PARTITION BY t.topic ORDER BY t.period), 1),
            1
        )                                           AS views_pct_change,

        -- Watch % delta
        ROUND(
            t.avg_watch_pct
            - LAG(t.avg_watch_pct) OVER (PARTITION BY t.topic ORDER BY t.period),
            2
        )                                           AS watch_pct_delta,

        -- Engagement delta
        ROUND(
            t.period_engagement_ratio
            - LAG(t.period_engagement_ratio) OVER (PARTITION BY t.topic ORDER BY t.period),
            4
        )                                           AS engagement_ratio_delta,

        -- Discord delta
        t.discord_messages
            - LAG(t.discord_messages) OVER (PARTITION BY t.topic ORDER BY t.period)
                                                    AS discord_messages_delta,
        ROUND(
            (t.discord_messages
             - LAG(t.discord_messages) OVER (PARTITION BY t.topic ORDER BY t.period))
            * 100.0
            / GREATEST(LAG(t.discord_messages) OVER (PARTITION BY t.topic ORDER BY t.period), 1),
            1
        )                                           AS discord_pct_change,

        -- Upload consistency delta
        t.videos_published
            - LAG(t.videos_published) OVER (PARTITION BY t.topic ORDER BY t.period)
                                                    AS upload_frequency_delta,

        -- Upload gap flag (went from publishing to zero)
        CASE
            WHEN t.videos_published = 0
             AND LAG(t.videos_published) OVER (PARTITION BY t.topic ORDER BY t.period) > 0
            THEN 1 ELSE 0
        END                                         AS flag_upload_gap
    FROM trends_raw AS t
),

-- ---------------------------------------------------------------------------
-- 4. Resonance Score per Period (mirrors resonance.sql formula)
-- ---------------------------------------------------------------------------
resonance_trend AS (
    SELECT
        d.period,
        d.topic,
        ROUND(
            LEAST(COALESCE(d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
          + LEAST(d.discord_messages / 50.0, 1.0) * 30.0
          + LEAST(d.period_engagement_ratio * 20.0, 20.0)
          + COALESCE(d.discord_sentiment_ratio, 0) * 10.0,
            1
        )                                           AS period_resonance_score,
        -- Momentum label vs previous period
        ROUND(
            (
                LEAST(COALESCE(d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
              + LEAST(d.discord_messages / 50.0, 1.0) * 30.0
              + LEAST(d.period_engagement_ratio * 20.0, 20.0)
              + COALESCE(d.discord_sentiment_ratio, 0) * 10.0
            )
            - LAG(
                LEAST(COALESCE(d.avg_watch_pct, 0) / 100.0, 1.0) * 40.0
              + LEAST(d.discord_messages / 50.0, 1.0) * 30.0
              + LEAST(d.period_engagement_ratio * 20.0, 20.0)
              + COALESCE(d.discord_sentiment_ratio, 0) * 10.0
              ) OVER (PARTITION BY d.topic ORDER BY d.period),
            1
        )                                           AS resonance_delta
    FROM trends_delta AS d
),

-- ---------------------------------------------------------------------------
-- 5. Topic Momentum — fastest-growing topics
-- ---------------------------------------------------------------------------
topic_momentum AS (
    SELECT
        rt.topic,
        COUNT(*)                                    AS periods_tracked,
        ROUND(AVG(rt.period_resonance_score), 1)    AS avg_resonance,
        ROUND(AVG(rt.resonance_delta), 1)           AS avg_resonance_delta,
        ROUND(MAX(rt.period_resonance_score), 1)    AS peak_resonance,
        SUM(CASE WHEN rt.resonance_delta > 0 THEN 1 ELSE 0 END) AS rising_periods,
        SUM(CASE WHEN rt.resonance_delta < 0 THEN 1 ELSE 0 END) AS declining_periods,
        CASE
            WHEN AVG(rt.resonance_delta) > 3   THEN 'rising'
            WHEN AVG(rt.resonance_delta) < -3  THEN 'declining'
            ELSE 'stable'
        END                                         AS topic_trajectory,
        ROW_NUMBER() OVER (ORDER BY AVG(rt.resonance_delta) DESC) AS momentum_rank
    FROM resonance_trend AS rt
    GROUP BY rt.topic
)

-- =============================================================================
-- FINAL SELECT — one row per (period, topic)
-- =============================================================================
SELECT
    -- Identity & time
    td.period,
    td.topic,

    -- Upload consistency
    td.videos_published,
    td.upload_frequency_delta,
    td.flag_upload_gap,

    -- YouTube raw metrics
    td.total_views,
    td.views_delta,
    td.views_pct_change,
    td.total_likes,
    td.total_comments,
    td.avg_ctr_percent,

    -- Retention quality
    td.avg_watch_pct,
    td.watch_pct_delta,

    -- Engagement quality
    td.period_engagement_ratio,
    td.engagement_ratio_delta,

    -- Community (Discord) trend
    td.discord_messages,
    td.discord_messages_delta,
    td.discord_pct_change,
    td.discord_active_members,
    td.discord_replies,
    td.discord_sentiment_ratio,

    -- Sheets conversion signals
    td.cta_clicks,
    td.email_signups,
    td.poll_responses,

    -- Resonance trend
    rt.period_resonance_score,
    rt.resonance_delta,
    CASE
        WHEN rt.resonance_delta > 5  THEN 'rising'
        WHEN rt.resonance_delta < -5 THEN 'declining'
        ELSE 'stable'
    END                                             AS momentum_label,

    -- Topic momentum aggregates
    tm.avg_resonance                                AS topic_avg_resonance,
    tm.avg_resonance_delta                          AS topic_avg_resonance_delta,
    tm.topic_trajectory,
    tm.momentum_rank                                AS topic_momentum_rank,
    tm.rising_periods                               AS topic_rising_periods,
    tm.declining_periods                            AS topic_declining_periods,

    -- Source attribution
    td.src_youtube,
    td.src_discord,
    td.src_sheets,
    CONCAT_WS(' · ',
        CASE WHEN td.src_youtube = 1 THEN 'YouTube' END,
        CASE WHEN td.src_discord = 1 THEN 'Discord' END,
        CASE WHEN td.src_sheets  = 1 THEN 'Sheets'  END
    )                                               AS source_attribution

FROM trends_delta AS td

LEFT JOIN resonance_trend AS rt
    ON  rt.period = td.period
    AND rt.topic  = td.topic

LEFT JOIN topic_momentum AS tm
    ON  tm.topic = td.topic

WHERE tm.momentum_rank <= :top_n
   OR tm.momentum_rank IS NULL

ORDER BY
    td.period DESC,
    tm.momentum_rank ASC NULLS LAST,
    td.total_views DESC;
