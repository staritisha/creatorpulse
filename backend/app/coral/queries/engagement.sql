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
-- Features covered:
--   1  Cross-source JOIN        (YouTube + Discord + Sheets)
--   2  Video engagement metrics (views, likes, comments, engagement rate)
--   3  Discord resonance        (message count, unique authors, spike flag)
--   4  Watch % integration      (from Sheets engagement log)
--   5  Resonance score inputs   (all weighted inputs in one row)
--   6  Topic performance        (content_category + topic per video)
--   7  Timeframe filtering      (:timeframe_days WHERE clause)
--   8  High performer flag      (is_top_performer)
--   9  Underperformer flag      (is_underperformer + reason)
--   10 Trend analysis columns   (week_start for time-series grouping)
--   11 AI context columns       (manual_notes, experiment_tag, creator signals)
--   12 Query optimisation       (LEFT JOINs, COALESCE, LIMIT, early filter)
--   13 Coral-compatible schema  (source.table naming convention)
--   14 Mock mode compatible     (no source-specific functions; pure SQL)
--   15 Reusable foundation      (other .sql files can SELECT FROM this shape)
-- =============================================================================

SELECT

    -- ── Video identity ────────────────────────────────────────────────────────
    y.video_id,
    y.title,
    y.topic,
    y.thumbnail_url,
    y.published_at,
    DATE_TRUNC('week', y.published_at)      AS week_start,          -- 10. trend grouping

    -- ── 2. YouTube engagement metrics ────────────────────────────────────────
    y.view_count,
    y.like_count,
    y.comment_count,
    ROUND(
        (COALESCE(y.like_count, 0) + COALESCE(y.comment_count, 0))::NUMERIC
        / NULLIF(y.view_count, 0) * 100,
        2
    )                                       AS engagement_rate_pct,

    -- ── 3. Discord community resonance ───────────────────────────────────────
    COALESCE(d.message_count,    0)         AS discord_msgs,
    COALESCE(d.unique_authors,   0)         AS discord_unique_authors,
    COALESCE(d.reply_count,      0)         AS discord_replies,
    COALESCE(d.reaction_count,   0)         AS discord_reactions,
    COALESCE(d.is_spike_day, false)         AS community_spike,
    COALESCE(d.spike_ratio,      1.0)       AS community_spike_ratio,

    -- ── 4. Retention data from Sheets ────────────────────────────────────────
    COALESCE(s.watch_pct,           0)      AS watch_pct,
    COALESCE(s.ctr,                 0)      AS ctr,
    COALESCE(s.avg_view_duration,   0)      AS avg_view_duration_secs,

    -- ── 6. Topic / category context ──────────────────────────────────────────
    COALESCE(s.content_category, y.topic, 'General') AS content_category,

    -- ── 11. AI context — creator signals ─────────────────────────────────────
    COALESCE(s.manual_notes,    '')         AS creator_notes,
    COALESCE(s.experiment_tag,  '')         AS experiment_tag,
    COALESCE(s.resonance_score, NULL)       AS manual_resonance_score,

    -- ── 5. Resonance score inputs (all four weighted components) ─────────────
    -- Full score computed in scoring/resonance_score.py using these columns:
    --   watch_pct × 0.40 + discord_msgs_norm × 0.40 + engagement_rate_pct × 0.20
    ROUND(
        COALESCE(s.watch_pct, 0) * 0.40
        + LEAST(COALESCE(d.message_count, 0)::NUMERIC / 100.0, 100.0) * 0.40
        + ROUND(
            (COALESCE(y.like_count, 0) + COALESCE(y.comment_count, 0))::NUMERIC
            / NULLIF(y.view_count, 0) * 100, 2
          ) * 0.20,
        2
    )                                       AS resonance_proxy,

    -- ── 8. High performer flag ────────────────────────────────────────────────
    CASE
        WHEN y.view_count          >= 5000
         AND COALESCE(s.watch_pct,  0) >= 60
         AND COALESCE(d.message_count, 0) >= 50
            THEN true
        ELSE false
    END                                     AS is_top_performer,

    -- ── 9. Underperformer flag + reason ───────────────────────────────────────
    CASE
        WHEN y.view_count >= 5000
         AND (
                COALESCE(s.watch_pct, 0) < 35
             OR COALESCE(d.message_count, 0) < 5
             OR (
                    COALESCE(y.like_count, 0) + COALESCE(y.comment_count, 0)
                )::NUMERIC / NULLIF(y.view_count, 0) < 0.01
         )
            THEN true
        ELSE false
    END                                     AS is_underperformer,

    CASE
        WHEN y.view_count >= 5000 AND COALESCE(s.watch_pct, 0) < 35
         AND COALESCE(d.message_count, 0) < 5
            THEN 'low_retention_and_community_silence'
        WHEN y.view_count >= 5000 AND COALESCE(s.watch_pct, 0) < 35
            THEN 'low_retention'
        WHEN y.view_count >= 5000 AND COALESCE(d.message_count, 0) < 5
            THEN 'community_silence'
        WHEN y.view_count >= 5000
         AND (COALESCE(y.like_count, 0) + COALESCE(y.comment_count, 0))::NUMERIC
             / NULLIF(y.view_count, 0) < 0.01
            THEN 'low_engagement'
        ELSE 'none'
    END                                     AS underperform_reason

-- ── 1 & 13. Cross-source JOINs (Coral source.table convention) ───────────────
FROM youtube.videos y

-- 3. Discord: match by topic + publish date (±1 day window via activity_date)
LEFT JOIN discord.messages_summary d
    ON  d.topic          = y.topic
    AND d.activity_date  = DATE(y.published_at)

-- 4. Sheets: match by video_id for watch% and creator notes
LEFT JOIN gsheets.engagement_log s
    ON  s.video_id       = y.video_id

-- ── 7. Timeframe filter (12. applied early for query optimisation) ────────────
WHERE
    y.published_at >= CURRENT_DATE - INTERVAL ':timeframe_days days'

-- ── 12. Ordered for analytics — most recent first, then by views ──────────────
ORDER BY
    y.published_at DESC,
    y.view_count   DESC

-- ── 12. Row cap — keeps demo fast; coral_client enforces MAX_QUERY_ROWS too ───
LIMIT 200
