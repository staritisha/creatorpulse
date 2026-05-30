-- =============================================================================
-- coral/queries/engagement.sql
-- CreatorPulse — Master Creator Engagement Query
-- =============================================================================
-- Role: Join YouTube × Discord × Google Sheets into one unified engagement
--       truth table. Every insight, score, and recommendation starts here.
--
-- Params:
--   :timeframe_days   — look-back window (default 30; pass 7, 30, or 90)
--
-- Column mapping (actual JSONL schema):
--   youtube.videos    → video_id, title, topic, published_at, views, likes,
--                       comments, watch_pct, ctr_percent, avg_view_duration_sec,
--                       shares, resonance_score, watch_time_minutes, impressions
--   discord.messages  → message_id, video_ref, author, channel, content,
--                       timestamp, sentiment, reply_count, total_reactions
--   gsheets.engagement_log → video_id, date, cta_clicks, link_clicks,
--                       email_signups, merch_clicks, affiliate_clicks,
--                       poll_responses, notes
--
-- Join keys:
--   discord  → d.video_ref  = y.video_id   (direct video reference)
--   sheets   → s.video_id   = y.video_id   (direct video reference)
--
-- Features covered:
--   1  Cross-source JOIN        (YouTube + Discord + Sheets)
--   2  Video engagement metrics (views, likes, comments, engagement rate)
--   3  Discord resonance        (msg count, unique authors, reactions, spike flag)
--   4  Watch % + CTR from YouTube source
--   5  Resonance score inputs   (all weighted inputs in one row)
--   6  Topic performance        (topic per video)
--   7  Timeframe filtering      (:timeframe_days WHERE clause)
--   8  High performer flag      (is_top_performer)
--   9  Underperformer flag      (is_underperformer + reason)
--  10  Trend analysis columns   (week_start for time-series grouping)
--  11  Creator signals          (creator notes from sheets)
--  12  Query optimisation       (subquery aggregation, COALESCE, LIMIT)
--  13  Coral-compatible schema  (source.table naming convention)
--  14  Mock mode compatible     (no source-specific functions; pure SQL)
--  15  Reusable foundation      (other .sql files can SELECT FROM this shape)
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Discord subquery: aggregate raw message rows → one row per video_ref
-- This replaces the old discord.messages_summary (which doesn't exist).
-- The raw discord.messages table has one row per message, so we aggregate
-- here before joining.
-- ---------------------------------------------------------------------------
WITH discord_agg AS (
    SELECT
        d.video_ref                                AS video_id,
        COUNT(d.message_id)                        AS message_count,
        COUNT(DISTINCT d.author)                   AS unique_authors,
        SUM(d.reply_count)                         AS reply_count,
        SUM(d.total_reactions)                     AS reaction_count,
        -- Spike flag: more than 10 messages on one video = above-average buzz
        CASE WHEN COUNT(d.message_id) >= 10
             THEN true ELSE false
        END                                        AS is_spike_day,
        -- Positive sentiment ratio (0.0 – 1.0)
        ROUND(
            SUM(CASE WHEN d.sentiment = 'positive' THEN 1.0 ELSE 0.0 END)
            / NULLIF(COUNT(d.message_id), 0),
            3
        )                                          AS positive_sentiment_ratio
    FROM discord.messages d
    WHERE d.video_ref IS NOT NULL
      AND d.video_ref != ''
    GROUP BY d.video_ref
),

-- ---------------------------------------------------------------------------
-- Sheets subquery: aggregate engagement rows → one row per video_id
-- Sheets has multiple rows per video (one per date), so we SUM them up.
-- ---------------------------------------------------------------------------
sheets_agg AS (
    SELECT
        s.video_id,
        SUM(s.cta_clicks)        AS total_cta_clicks,
        SUM(s.link_clicks)       AS total_link_clicks,
        SUM(s.email_signups)     AS total_email_signups,
        SUM(s.merch_clicks)      AS total_merch_clicks,
        SUM(s.affiliate_clicks)  AS total_affiliate_clicks,
        SUM(s.poll_responses)    AS total_poll_responses,
        -- Concatenate non-empty notes with a separator for context
        MAX(s.notes)             AS latest_notes
    FROM gsheets.engagement_log s
    GROUP BY s.video_id
)

SELECT

    -- ── Video identity ────────────────────────────────────────────────────────
    y.video_id,
    y.title,
    y.topic,
    y.published_at,

    -- ── 10. Trend grouping (week bucket for time-series charts) ──────────────
    -- DATE_TRUNC not available in all Coral backends; use published_at directly
    y.published_at                                  AS week_start,

    -- ── 2. YouTube engagement metrics ────────────────────────────────────────
    -- Actual column names in youtube.videos JSONL: views, likes, comments
    y.views                                         AS view_count,
    y.likes                                         AS like_count,
    y.comments                                      AS comment_count,
    y.shares,
    y.impressions,
    y.watch_time_minutes,
    ROUND(
        (COALESCE(y.likes, 0) + COALESCE(y.comments, 0)) * 1.0
        / NULLIF(y.views, 0) * 100,
        2
    )                                               AS engagement_rate_pct,

    -- ── 4. Watch % + CTR — live from YouTube source ──────────────────────────
    -- watch_pct and ctr_percent exist directly on youtube.videos JSONL
    COALESCE(y.watch_pct,           0.0)            AS watch_pct,
    COALESCE(y.ctr_percent,         0.0)            AS ctr,
    COALESCE(y.avg_view_duration_sec, 0)            AS avg_view_duration_secs,

    -- ── Pre-computed resonance score from YouTube source ─────────────────────
    COALESCE(y.resonance_score,     0.0)            AS resonance_score,

    -- ── 3. Discord community resonance ───────────────────────────────────────
    COALESCE(da.message_count,      0)              AS discord_msgs,
    COALESCE(da.unique_authors,     0)              AS discord_unique_authors,
    COALESCE(da.reply_count,        0)              AS discord_replies,
    COALESCE(da.reaction_count,     0)              AS discord_reactions,
    COALESCE(da.is_spike_day,       false)          AS community_spike,
    COALESCE(da.positive_sentiment_ratio, 0.0)      AS positive_sentiment_ratio,

    -- ── Google Sheets CTA + conversion signals ───────────────────────────────
    COALESCE(sa.total_cta_clicks,        0)         AS cta_clicks,
    COALESCE(sa.total_link_clicks,       0)         AS link_clicks,
    COALESCE(sa.total_email_signups,     0)         AS email_signups,
    COALESCE(sa.total_merch_clicks,      0)         AS merch_clicks,
    COALESCE(sa.total_affiliate_clicks,  0)         AS affiliate_clicks,
    COALESCE(sa.total_poll_responses,    0)         AS poll_responses,

    -- ── 6. Topic / category context ──────────────────────────────────────────
    COALESCE(y.topic, 'General')                    AS content_category,

    -- ── 11. Creator signals ───────────────────────────────────────────────────
    COALESCE(sa.latest_notes, '')                   AS creator_notes,

    -- ── 5. Resonance proxy — computed from raw signals ───────────────────────
    -- watch_pct×0.40 + discord_msgs_norm×0.30 + engagement_rate×0.20
    --   + sentiment×0.10
    ROUND(
        COALESCE(y.watch_pct, 0.0) * 0.40
        + LEAST(COALESCE(da.message_count, 0) * 1.0 / 10.0, 100.0) * 0.30
        + ROUND(
            (COALESCE(y.likes, 0) + COALESCE(y.comments, 0)) * 1.0
            / NULLIF(y.views, 0) * 100, 2
          ) * 0.20
        + COALESCE(da.positive_sentiment_ratio, 0.0) * 100 * 0.10,
        2
    )                                               AS resonance_proxy,

    -- ── 8. High performer flag ────────────────────────────────────────────────
    CASE
        WHEN y.views                           >= 50000
         AND COALESCE(y.watch_pct,       0.0)  >= 60.0
         AND COALESCE(da.message_count,  0)    >= 10
            THEN true
        ELSE false
    END                                             AS is_top_performer,

    -- ── 9. Underperformer flag + reason ───────────────────────────────────────
    CASE
        WHEN y.views >= 10000
         AND (
                COALESCE(y.watch_pct, 0.0)    < 35.0
             OR COALESCE(da.message_count, 0) < 3
             OR (COALESCE(y.likes, 0) + COALESCE(y.comments, 0)) * 1.0
                / NULLIF(y.views, 0) < 0.01
         )
            THEN true
        ELSE false
    END                                             AS is_underperformer,

    CASE
        WHEN y.views >= 10000
         AND COALESCE(y.watch_pct, 0.0)    < 35.0
         AND COALESCE(da.message_count, 0) < 3
            THEN 'low_retention_and_community_silence'
        WHEN y.views >= 10000
         AND COALESCE(y.watch_pct, 0.0)    < 35.0
            THEN 'low_retention'
        WHEN y.views >= 10000
         AND COALESCE(da.message_count, 0) < 3
            THEN 'community_silence'
        WHEN y.views >= 10000
         AND (COALESCE(y.likes, 0) + COALESCE(y.comments, 0)) * 1.0
             / NULLIF(y.views, 0) < 0.01
            THEN 'low_engagement'
        ELSE 'none'
    END                                             AS underperform_reason

-- ── 1 & 13. Cross-source JOINs using actual JSONL join keys ──────────────────
FROM youtube.videos y

-- Discord: join on video_ref (raw message rows pre-aggregated above)
LEFT JOIN discord_agg da
    ON  da.video_id = y.video_id

-- Sheets: join on video_id (rows pre-aggregated above)
LEFT JOIN sheets_agg sa
    ON  sa.video_id = y.video_id

-- ── 7. Timeframe filter ───────────────────────────────────────────────────────
WHERE
    y.published_at >= CURRENT_DATE - INTERVAL ':timeframe_days days'

-- ── 12. Order: most recent first, then by views ───────────────────────────────
ORDER BY
    y.published_at DESC,
    y.views        DESC

-- ── 12. Row cap ───────────────────────────────────────────────────────────────
LIMIT 200
