#!/usr/bin/env python3
"""
scripts/convert_mock_to_jsonl.py
─────────────────────────────────
Converts CreatorPulse mock JSON files → JSONL format for Coral file backend.
Also patches coral_specs YAML files with the correct absolute path.

Run once before starting the backend:
    python scripts/convert_mock_to_jsonl.py

Then register sources:
    coral source lint coral_specs/youtube.yaml
    coral source lint coral_specs/discord.yaml
    coral source lint coral_specs/gsheets.yaml
    coral source add --file coral_specs/youtube.yaml
    coral source add --file coral_specs/discord.yaml
    coral source add --file coral_specs/gsheets.yaml
"""
import json
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
MOCK_DIR     = BACKEND_ROOT / "app" / "data" / "mock"
CORAL_DIR    = BACKEND_ROOT / "app" / "data" / "coral_sources"
SPECS_DIR    = BACKEND_ROOT.parent / "coral_specs"   # project root / coral_specs

CORAL_DIR.mkdir(parents=True, exist_ok=True)


def flatten_discord_message(m: dict) -> dict:
    """reactions dict → total_reactions int (Coral needs flat scalar columns)."""
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


def patch_yaml_paths(abs_dir: str) -> None:
    """Replace the REPLACE_WITH_ABSOLUTE_PATH placeholder in all spec YAMLs."""
    if not SPECS_DIR.exists():
        print(f"  ⚠  coral_specs dir not found at {SPECS_DIR} — skipping YAML patch")
        return
    for yaml_file in SPECS_DIR.glob("*.yaml"):
        content = yaml_file.read_text(encoding="utf-8")
        if "REPLACE_WITH_ABSOLUTE_PATH" in content:
            patched = content.replace("REPLACE_WITH_ABSOLUTE_PATH", abs_dir)
            yaml_file.write_text(patched, encoding="utf-8")
            print(f"  ✓  Patched path in {yaml_file.name}")


def main() -> None:
    print("CreatorPulse — Mock JSON → JSONL converter for Coral\n")

    errors = []

    # ── YouTube ──────────────────────────────────────────────────────────────
    try:
        raw = json.loads((MOCK_DIR / "youtube_mock.json").read_text())
        videos = raw.get("videos", [])
        out = CORAL_DIR / "youtube_videos.jsonl"
        n = write_jsonl(videos, out)
        print(f"  ✓  youtube_videos.jsonl   — {n} rows → {out}")
    except Exception as e:
        errors.append(f"YouTube: {e}")
        print(f"  ✗  youtube_mock.json failed: {e}")

    # ── Discord ───────────────────────────────────────────────────────────────
    try:
        raw = json.loads((MOCK_DIR / "discord_mock.json").read_text())
        messages = [flatten_discord_message(m) for m in raw.get("messages", [])]
        out = CORAL_DIR / "discord_messages.jsonl"
        n = write_jsonl(messages, out)
        print(f"  ✓  discord_messages.jsonl — {n} rows → {out}")
    except Exception as e:
        errors.append(f"Discord: {e}")
        print(f"  ✗  discord_mock.json failed: {e}")

    # ── Sheets ────────────────────────────────────────────────────────────────
    try:
        raw = json.loads((MOCK_DIR / "sheets_mock.json").read_text())
        rows = raw.get("rows", [])
        out = CORAL_DIR / "sheets_engagement.jsonl"
        n = write_jsonl(rows, out)
        print(f"  ✓  sheets_engagement.jsonl — {n} rows → {out}")
    except Exception as e:
        errors.append(f"Sheets: {e}")
        print(f"  ✗  sheets_mock.json failed: {e}")

    # ── Patch YAML specs with real absolute path ───────────────────────────
    print(f"\nPatching coral_specs YAML files with path: {CORAL_DIR}")
    patch_yaml_paths(str(CORAL_DIR))

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if errors:
        print(f"Completed with {len(errors)} error(s):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("All JSONL files written successfully.")
        print()
        print("Next steps:")
        print("  coral source lint coral_specs/youtube.yaml")
        print("  coral source lint coral_specs/discord.yaml")
        print("  coral source lint coral_specs/gsheets.yaml")
        print("  coral source add --file coral_specs/youtube.yaml")
        print("  coral source add --file coral_specs/discord.yaml")
        print("  coral source add --file coral_specs/gsheets.yaml")
        print()
        print("Verify with the real cross-source JOIN:")
        print("""  coral sql "
    SELECT y.title, y.views, y.watch_pct,
           d.total_reactions, d.sentiment,
           s.email_signups, s.cta_clicks
    FROM youtube.videos y
    LEFT JOIN discord.messages d ON d.video_ref = y.video_id
    LEFT JOIN gsheets.engagement_log s ON s.video_id = y.video_id
    ORDER BY y.views DESC LIMIT 5
  " """)


if __name__ == "__main__":
    main()
