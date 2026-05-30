-- =============================================================================
-- coral/queries/resonance.sql
-- CreatorPulse · Signature Intelligence Query
-- =============================================================================
-- Role: The single source of truth for Creator Resonance.
--       Joins YouTube performance + Discord community activity + Google Sheets
--       engagement data into one measurable per-video signal.
--
-- Powers:  routes/analytics.py · ai/insight_engine.py · scoring/resonance_score.py
-- Params:  :timeframe_days  — lookback window (7 | 30 | 90), default 30
--          :topic_filter    — optional topic string filter, NULL = all
--          :top_n           — max rows returned, default 20
--
-- Schema (actual JSONL columns):
--   youtube.videos       → video_id, title, published_at, topic, views,
--                          watch_pct, likes, comments, ctr_percent,
--                          avg_view_duration_sec, resonance_score,
--                          watch_time_minutes, shares, impressions
--   discord.messages     → message_id, video_ref, author, channel, content,
--                          timestamp, sentiment, reply_count, total_reactions
--   gsheets.engagement_log → date, video_id, source, cta_clicks, link_clicks,
--                          email_signups, merch_clicks, affiliate_clicks,
--                          poll_responses, notes
-- =============================================================================

WITH

-- ---------------------------------------------------------------------------
-- 1a. YouTube videos in the requested timeframe
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
        v.watch_pct,
        v.ctr_percent,
        v.avg_view_duration_sec,
        v.resonance_score                   AS precomputed_resonance,
        -- Normalised engagement quality: (likes + comments) / max(views, 1)
        ROUND(
            (v.likes + v.comments) * 1.0 / GREATEST(v.views, 1),
            4
        )                                   AS engagement_ratio
    FROM youtube.videos AS v
    WHERE v.published_at >= NOW() - INTERVAL ':timeframe_days' DAY
      AND (:topic_filter IS NULL OR v.topic ILIKE '%' || :topic_filter || '%')
),

-- ---------------------------------------------------------------------------
-- 1b. Discord messages — aggregated per video_ref (one row per video)
--     Joins on video_ref = video_id (direct reference in JSONL)
-- ---------------------------------------------------------------------------
disc_agg AS (
    SELECT
        m.video_ref                             AS video_id,
        COUNT(m.message_id)                     AS msg_count,
        COUNT(DISTINCT m.author)                AS unique_authors,
        SUM(m.reply_count)                      AS reply_chains,
        SUM(m.total_reactions)                  AS total_reactions,
        -- Positive sentiment ratio (0.0 – 1.0)
        ROUND(
            SUM(CASE WHEN m.sentiment = 'positive' THEN 1.0 ELSE 0.0 END)
            / NULLIF(COUNT(m.message_id), 0),
            3
        )                                       AS positive_sentiment_ratio,
        -- Spike flag: >=10 messages about one video = above-average buzz
        CASE WHEN COUNT(m.message_id) >= 10
             THEN true ELSE false
        END                                     AS is_community_spike
    FROM discord.messages AS m
    WHERE m.video_ref IS NOT NULL
      AND m.video_ref != ''
    GROUP BY m.video_ref
),

-- ---------------------------------------------------------------------------
-- 1c. Google Sheets — aggregated per video_id (SUM across multiple date rows)
-- ---------------------------------------------------------------------------
sheets_agg AS (
    SELECT
        s.video_id,
        SUM(s.cta_clicks)           AS total_cta_clicks,
        SUM(s.email_signups)        AS total_email_signups,
        SUM(s.affiliate_clicks)     AS total_affiliate_clicks,
        SUM(s.poll_responses)       AS total_poll_responses,
        MAX(s.notes)                AS latest_notes
    FROM gsheets.engagement_log AS s
    GROUP BY s.video_id
),

-- ---------------------------------------------------------------------------
-- 2. Cross-Source Resonance JOIN
--    LEFT JOINs so a video is never dropped when Discord or Sheets data
--    is absent; NULLs are coalesced to safe zeros.
-- ---------------------------------------------------------------------------
resonance_raw AS (
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
        yt.watch_pct,
        yt.ctr_percent,
        yt.engagement_ratio,
        yt.precomputed_resonance,

        -- Discord signals
        COALESCE(da.msg_count,                0)     AS discord_msg_count,
        COALESCE(da.unique_authors,           0)     AS discord_unique_authors,
        COALESCE(da.reply_chains,             0)     AS discord_reply_chains,
        COALESCE(da.total_reactions,          0)     AS discord_total_reactions,
        COALESCE(da.positive_sentiment_ratio, 0.0)   AS positive_sentiment_ratio,
        COALESCE(da.is_community_spike,       false) AS is_community_spike,

        -- Sheets CTA / conversion signals
        COALESCE(sa.total_cta_clicks,         0)     AS cta_clicks,
        COALESCE(sa.total_email_signups,      0)     AS email_signups,
        COALESCE(sa.total_affiliate_clicks,   0)     AS affiliate_clicks,
        COALESCE(sa.total_poll_responses,     0)     AS poll_responses,

        -- Source attribution flags
        CASE WHEN yt.video_id IS NOT NULL THEN 1 ELSE 0 END AS src_youtube,
        CASE WHEN da.video_id IS NOT NULL THEN 1 ELSE 0 END AS src_discord,
        CASE WHEN sa.video_id IS NOT NULL THEN 1 ELSE 0 END AS src_sheets

    FROM yt_videos AS yt
    LEFT JOIN disc_agg  AS da ON da.video_id = yt.video_id
    LEFT JOIN sheets_agg AS sa ON sa.video_id = yt.video_id
),

-- ---------------------------------------------------------------------------
-- 3. Weighted Resonance Score
--    Mirrors scoring/resonance_score.py formula exactly:
--      watch_pct  × 0.40  (audience retention)
--      discord    × 0.30  (community discussion strength)
--      engagement × 0.20  (likes + comments quality)
--      sentiment  × 0.10  (positive community mood)
-- ---------------------------------------------------------------------------
resonance_scored AS (
    SELECT
        r.*,

        -- Component A: audience retention (0–40 pts)
        ROUND(LEAST(r.watch_pct / 100.0, 1.0) * 40.0, 2)          AS score_watch,

        -- Component B: community discussion (0–30 pts; baseline = 50 msgs)
        ROUND(LEAST(r.discord_msg_count / 50.0, 1.0) * 30.0, 2)   AS score_discord,

        -- Component C: engagement quality (0–20 pts)
        ROUND(LEAST(r.engagement_ratio * 20.0, 20.0), 2)           AS score_engagement,

        -- Component D: positive sentiment (0–10 pts)
        ROUND(r.positive_sentiment_ratio * 10.0, 2)                AS score_sentiment,

        -- Anomaly flags for ai/detectors.py
        CASE WHEN r.views > 50000 AND r.watch_pct < 30
             THEN 1 ELSE 0 END                                      AS flag_high_views_low_retention,
        CASE WHEN r.views > 50000 AND r.discord_msg_count < 5
             THEN 1 ELSE 0 END                                      AS flag_high_views_low_community,
        CASE WHEN r.is_community_spike
             THEN 1 ELSE 0 END                                      AS flag_community_burst
    FROM resonance_raw AS r
),

-- ---------------------------------------------------------------------------
-- 4. Final Resonance Score + Ranking
-- ---------------------------------------------------------------------------
resonance_final AS (
    SELECT
        s.*,
        ROUND(s.score_watch + s.score_discord + s.score_engagement + s.score_sentiment, 1)
                                                AS resonance_score,
        CASE
            WHEN (s.score_watch + s.score_discord + s.score_engagement + s.score_sentiment) >= 80
                THEN 'exceptional'
            WHEN (s.score_watch + s.score_discord + s.score_engagement + s.score_sentiment) >= 60
                THEN 'strong'
            WHEN (s.score_watch + s.score_discord + s.score_engagement + s.score_sentiment) >= 40
                THEN 'average'
            ELSE 'poor'
        END                                     AS resonance_tier,
        ROW_NUMBER() OVER (
            ORDER BY (s.score_watch + s.score_discord + s.score_engagement + s.score_sentiment) DESC
        )                                       AS resonance_rank
    FROM resonance_scored AS s
),

-- ---------------------------------------------------------------------------
-- 5. Topic-Level Aggregation — "What topic resonates most?"
-- ---------------------------------------------------------------------------
topic_resonance AS (
    SELECT
        f.topic,
        COUNT(*)                                AS video_count,
        ROUND(AVG(f.resonance_score), 1)        AS avg_resonance,
        ROUND(MAX(f.resonance_score), 1)        AS peak_resonance,
        SUM(f.discord_msg_count)                AS total_discord_msgs,
        ROUND(AVG(f.watch_pct), 1)              AS avg_watch_pct,
        ROW_NUMBER() OVER (ORDER BY AVG(f.resonance_score) DESC) AS topic_rank
    FROM resonance_final AS f
    GROUP BY f.topic
)

-- =============================================================================
-- FINAL SELECT — one row per video, Claude-ready + dashboard-ready
-- =============================================================================
SELECT
    -- Identity
    f.video_id,
    f.title,
    f.topic,
    f.published_at,

    -- Raw signals (consumed by scoring/resonance_score.py)
    f.views,
    f.likes,
    f.comments,
    f.watch_pct,
    f.ctr_percent,
    f.engagement_ratio,
    f.discord_msg_count,
    f.discord_unique_authors,
    f.discord_reply_chains,
    f.discord_total_reactions,
    f.positive_sentiment_ratio,
    f.cta_clicks,
    f.email_signups,
    f.affiliate_clicks,

    -- Weighted components
    f.score_watch,
    f.score_discord,
    f.score_engagement,
    f.score_sentiment,

    -- Final score + classification
    f.resonance_score,
    f.resonance_tier,
    f.resonance_rank,

    -- Anomaly flags (consumed by ai/detectors.py)
    f.flag_high_views_low_retention,
    f.flag_high_views_low_community,
    f.flag_community_burst,

    -- Topic-level aggregates (denormalised for single-query AI context)
    tr.avg_resonance        AS topic_avg_resonance,
    tr.video_count          AS topic_video_count,
    tr.avg_watch_pct        AS topic_avg_watch_pct,
    tr.total_discord_msgs   AS topic_total_discord_msgs,
    tr.topic_rank           AS topic_overall_rank,

    -- Source attribution (drives frontend "YouTube · Discord · Sheets" badges)
    f.src_youtube,
    f.src_discord,
    f.src_sheets,
    CONCAT_WS(' · ',
        CASE WHEN f.src_youtube = 1 THEN 'YouTube' END,
        CASE WHEN f.src_discord = 1 THEN 'Discord' END,
        CASE WHEN f.src_sheets  = 1 THEN 'Sheets'  END
    )                       AS source_attribution

FROM resonance_final AS f

LEFT JOIN topic_resonance AS tr
    ON tr.topic = f.topic

WHERE f.resonance_rank <= :top_n

ORDER BY f.resonance_rank ASC;
