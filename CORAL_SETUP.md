# CreatorPulse — Coral Setup Commands (Run in Order)

## Step 1: Place files

```
your-project/
├── coral_specs/           ← NEW: copy coral_specs/ folder here
│   ├── youtube.yaml
│   ├── discord.yaml
│   └── gsheets.yaml
├── backend/
│   ├── app/
│   │   └── coral/
│   │       └── coral_client.py   ← REPLACE with new version
│   └── scripts/
│       └── convert_mock_to_jsonl.py   ← NEW
```

## Step 2: Convert mock JSON → JSONL + patch YAML paths

```bash
cd backend
python scripts/convert_mock_to_jsonl.py
```

Expected output:
```
  ✓  youtube_videos.jsonl   — 18 rows
  ✓  discord_messages.jsonl — 50 rows
  ✓  sheets_engagement.jsonl — 30 rows
  ✓  Patched path in youtube.yaml
  ✓  Patched path in discord.yaml
  ✓  Patched path in gsheets.yaml
```

## Step 3: Lint all specs (no install, catches errors fast)

```bash
cd ..   # back to project root (where coral_specs/ lives)
coral source lint coral_specs/youtube.yaml
coral source lint coral_specs/discord.yaml
coral source lint coral_specs/gsheets.yaml
```

All three should say: `Source spec is valid`

## Step 4: Register sources with Coral

```bash
coral source add --file coral_specs/youtube.yaml
coral source add --file coral_specs/discord.yaml
coral source add --file coral_specs/gsheets.yaml
```

## Step 5: Test each source

```bash
coral source test youtube
coral source test discord
coral source test gsheets
```

## Step 6: Run the DEMO JOIN query (this is your money shot)

```bash
coral sql "
SELECT
    y.title,
    y.views,
    y.watch_pct,
    d.total_reactions,
    d.sentiment,
    s.email_signups,
    s.cta_clicks
FROM youtube.videos y
LEFT JOIN discord.messages d ON d.video_ref = y.video_id
LEFT JOIN gsheets.engagement_log s ON s.video_id = y.video_id
ORDER BY y.views DESC
LIMIT 5
"
```

If this returns rows with data from all 3 sources — you have real Coral execution.
Screenshot this terminal output for your submission.

## Step 7: Start the backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Watch for these log lines on startup:
```
✓ Coral spec lint passed: youtube.yaml
✓ Coral source registered: youtube.yaml
✓ Coral source registered: discord.yaml
✓ Coral source registered: gsheets.yaml
✓ Coral MCP server started (pid=XXXXX)
Coral ready (mode=local_file)
```

## What changed in coral_client.py

| Was | Now |
|-----|-----|
| `coral source add --type file --config {...}` | `coral source add --file spec.yaml` |
| `coral query --output json --sql "..."` | `coral sql "..."` |
| Fallback to mock on registration failure | Same, but now registration actually works |
| No MCP | `coral mcp` started on init |

## The demo SQL to show judges

```sql
-- CreatorPulse Resonance Query — 3 sources, 1 query, 0 glue code
SELECT
    y.title,
    y.views,
    y.watch_pct,
    COUNT(d.message_id)   AS discord_messages,
    SUM(d.total_reactions) AS community_reactions,
    SUM(s.email_signups)  AS email_signups
FROM youtube.videos y
LEFT JOIN discord.messages d ON d.video_ref = y.video_id
LEFT JOIN gsheets.engagement_log s ON s.video_id = y.video_id
GROUP BY y.title, y.views, y.watch_pct
ORDER BY y.views DESC
```

Point to this in your demo and say:
"YouTube, Discord, and Sheets joined in one SQL query.
No ETL. No API wrappers. No glue code. That's Coral."
