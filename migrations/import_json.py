"""Migrate stories.json to SQLite database."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import add_story, init_db, update_stage, _get_conn


def main():
    json_path = Path("stories.json")
    if not json_path.exists():
        print("stories.json not found")
        return

    with open(json_path) as f:
        stories = json.load(f)

    print(f"stories.json: {len(stories)}件")

    init_db()
    conn = _get_conn()

    imported = 0
    skipped = 0

    for s in stories:
        url = s.get("url", "")
        if not url:
            skipped += 1
            continue

        title = s.get("title", "")
        pub_date = s.get("pub_date", "")
        categories = s.get("categories", [])
        stage = s.get("stage", "pending")
        error = s.get("error")
        added_at = s.get("added_at", "")
        stages_completed = s.get("stages_completed", {})

        # Clean up running/failed stages
        if ":running" in stage or ":failed" in stage:
            stage = stage.split(":")[0]
            # Go back to previous stage
            from app.models import STAGES
            idx = STAGES.index(stage) if stage in STAGES else 0
            stage = STAGES[max(0, idx - 1)]

        # Insert story
        result = add_story(url=url, title=title, pub_date=pub_date, categories=categories)
        if result is None:
            skipped += 1
            continue

        # Update to correct stage
        if stage != "pending":
            conn.execute(
                "UPDATE stories SET stage = ?, error = ?, added_at = ?, updated_at = ? WHERE id = ?",
                (stage, error, added_at or result.added_at, result.updated_at, result.id),
            )

        # Import stage completions
        for comp_stage, comp_time in stages_completed.items():
            conn.execute(
                "INSERT OR IGNORE INTO stage_completions (story_id, stage, completed_at) VALUES (?, ?, ?)",
                (result.id, comp_stage, comp_time),
            )

        imported += 1
        if imported % 100 == 0:
            print(f"  {imported}件処理済...")
            conn.commit()

    conn.commit()
    print(f"\n完了: {imported}件インポート、{skipped}件スキップ")

    # Show stage counts
    rows = conn.execute("SELECT stage, COUNT(*) FROM stories GROUP BY stage").fetchall()
    for row in rows:
        print(f"  {row[0]}: {row[1]}件")


if __name__ == "__main__":
    main()
