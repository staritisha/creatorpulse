#!/usr/bin/env python3
"""
scripts/convert_mock_to_jsonl.py
─────────────────────────────────
Converts CreatorPulse mock JSON files → JSONL format for Coral file backend.
Also patches coral_specs YAML files with the correct absolute path — works on
any machine, any OS. Run this once before registering Coral sources.

Usage:
    python scripts/convert_mock_to_jsonl.py

Then register sources with Coral:
    coral source lint coral_specs/youtube.yaml
    coral source lint coral_specs/discord.yaml
    coral source lint coral_specs/gsheets.yaml
    coral source add --file coral_specs/youtube.yaml
    coral source add --file coral_specs/discord.yaml
    coral source add --file coral_specs/gsheets.yaml

Verify the cross-source JOIN:
    coral sql "
        SELECT y.title, y.views, y.watch_pct,
               d.total_reactions, d.sentiment,
               s.email_signups, s.cta_clicks
        FROM youtube.videos y
        LEFT JOIN discord.messages d ON d.video_ref = y.video_id
        LEFT JOIN gsheets.engagement_log s ON s.video_id = y.video_id
        ORDER BY y.views DESC LIMIT 5
    "
"""
import json
import re
import sys
from pathlib import Path

# ── Paths resolved from THIS file's location — always correct on any machine ──
SCRIPT_DIR   = Path(__file__).resolve().parent      # backend/scripts/
BACKEND_ROOT = SCRIPT_DIR.parent                    # backend/
MOCK_DIR     = BACKEND_ROOT / "app" / "data" / "mock"
CORAL_DIR    = BACKEND_ROOT / "app" / "data" / "coral_sources"
SPECS_DIR    = BACKEND_ROOT.parent / "coral_specs"  # project root / coral_specs

CORAL_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────

def flatten_discord_message(m: dict) -> dict:
    """Flatten nested reactions dict → total_reactions int for Coral."""
    reactions = m.get("reactions", {})
    total = sum(reactions.values()) if isinstance(reactions, dict) else 0
    return {
        "message_id":      m.get("message_id", ""),
        "channel":         m.get("channel", ""),
        "video_ref":       m.get("video_ref", ""),
        "author":          m.get("author", ""),
        "content":         m.get("content", ""),
        "timestamp":       m.get("timestamp", ""),
        "sentiment":       m.get("sentiment", "neutral"),
        "reply_count":     m.get("reply_count", 0),
        "total_reactions": total,
    }


def write_jsonl(rows: list, out_path: Path) -> int:
    """Write list of dicts as JSONL. Returns row count."""
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def patch_yaml_paths() -> None:
    """
    Rewrite the location: field in every coral_specs/*.yaml to point at
    CORAL_DIR on *this* machine, regardless of where the project lives.

    Replaces:
      - The placeholder:      file://REPLACE_WITH_ABSOLUTE_PATH/
      - Any previously-baked absolute path (from another machine)

    This makes the script fully idempotent — safe to run multiple times.
    """
    abs_dir = str(CORAL_DIR.resolve())

    if not SPECS_DIR.exists():
        print(f"  WARNING  coral_specs dir not found at {SPECS_DIR} — skipping YAML patch")
        return

    # Pattern matches any file:// absolute path ending in coral_sources[/]
    old_path_pattern = re.compile(r'file://[^\s"]+coral_sources/?')

    patched = 0
    for yaml_file in sorted(SPECS_DIR.glob("*.yaml")):
        if yaml_file.name.endswith(".save"):
            continue
        text = yaml_file.read_text(encoding="utf-8")

        # Step 1: replace any pre-baked absolute path
        new_text = old_path_pattern.sub(f"file://{abs_dir}/", text)

        # Step 2: replace the placeholder (handles fresh checkouts)
        new_text = new_text.replace("REPLACE_WITH_ABSOLUTE_PATH", abs_dir)

        yaml_file.write_text(new_text, encoding="utf-8")
        print(f"  OK  {yaml_file.name}")
        print(f"      location: file://{abs_dir}/")
        patched += 1

    if patched == 0:
        print("  INFO  No YAML files found in", SPECS_DIR)


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    print("CreatorPulse — Mock JSON → JSONL converter for Coral\n")

    errors = []

    # YouTube
    try:
        raw    = json.loads((MOCK_DIR / "youtube_mock.json").read_text())
        videos = raw.get("videos", [])
        n      = write_jsonl(videos, CORAL_DIR / "youtube_videos.jsonl")
        print(f"  OK  youtube_videos.jsonl      — {n} rows")
    except Exception as e:
        errors.append(f"YouTube: {e}")
        print(f"  FAIL  youtube_mock.json: {e}")

    # Discord
    try:
        raw      = json.loads((MOCK_DIR / "discord_mock.json").read_text())
        messages = [flatten_discord_message(m) for m in raw.get("messages", [])]
        n        = write_jsonl(messages, CORAL_DIR / "discord_messages.jsonl")
        print(f"  OK  discord_messages.jsonl   — {n} rows")
    except Exception as e:
        errors.append(f"Discord: {e}")
        print(f"  FAIL  discord_mock.json: {e}")

    # Sheets
    try:
        raw  = json.loads((MOCK_DIR / "sheets_mock.json").read_text())
        rows = raw.get("rows", [])
        n    = write_jsonl(rows, CORAL_DIR / "sheets_engagement.jsonl")
        print(f"  OK  sheets_engagement.jsonl  — {n} rows")
    except Exception as e:
        errors.append(f"Sheets: {e}")
        print(f"  FAIL  sheets_mock.json: {e}")

    # Patch YAML specs
    print(f"\nPatching coral_specs with absolute path to coral_sources/")
    patch_yaml_paths()

    # Summary
    print()
    if errors:
        print(f"Completed with {len(errors)} error(s):")
        for e in errors:
            print(f"  FAIL  {e}")
        sys.exit(1)
    else:
        print("All done. Next steps:")
        print()
        print("  coral source add --file coral_specs/youtube.yaml")
        print("  coral source add --file coral_specs/discord.yaml")
        print("  coral source add --file coral_specs/gsheets.yaml")
        print()
        print("Verify with:")
        print('  coral sql "SELECT y.title, d.sentiment, s.cta_clicks')
        print("              FROM youtube.videos y")
        print("              LEFT JOIN discord.messages d ON d.video_ref = y.video_id")
        print('              LEFT JOIN gsheets.engagement_log s ON s.video_id = y.video_id LIMIT 3"')


if __name__ == "__main__":
    main()
