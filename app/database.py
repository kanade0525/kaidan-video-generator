from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.models import STAGES, Story, infer_source_from_url, stages_for

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "kaidan.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT DEFAULT '',
            pub_date TEXT DEFAULT '',
            stage TEXT DEFAULT 'pending',
            error TEXT,
            added_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stage ON stories(stage);
        CREATE INDEX IF NOT EXISTS idx_url ON stories(url);

        CREATE TABLE IF NOT EXISTS story_categories (
            story_id INTEGER REFERENCES stories(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            PRIMARY KEY (story_id, category)
        );

        CREATE TABLE IF NOT EXISTS stage_completions (
            story_id INTEGER REFERENCES stories(id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            PRIMARY KEY (story_id, stage)
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            level TEXT NOT NULL,
            stage TEXT,
            story_id INTEGER,
            message TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logs_story ON logs(story_id);
        CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_story_categories_story ON story_categories(story_id);
        CREATE INDEX IF NOT EXISTS idx_stage_completions_story ON stage_completions(story_id);
    """)
    # Migrations: add columns if missing
    for col, definition in [
        ("youtube_video_id", "TEXT"),
        ("content_type", "TEXT DEFAULT 'long'"),
        ("author", "TEXT DEFAULT ''"),
        ("char_count", "INTEGER"),
        ("title_furigana", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT 'hhs'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE stories ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_content_type ON stories(content_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_content_type_stage ON stories(content_type, stage)")
    # Backfill source for rows where it was defaulted but should differ based on URL/content_type.
    # Existing shorts are all from kikikaikai; existing longs are all from hhs.
    conn.execute(
        "UPDATE stories SET source = 'kikikaikai' "
        "WHERE content_type = 'short' AND (source IS NULL OR source = '' OR source = 'hhs')"
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_story(
    row: sqlite3.Row,
    categories: list[str] | None = None,
    stages_completed: dict[str, str] | None = None,
) -> Story:
    """Convert a DB row to Story. Accepts pre-loaded relations to avoid N+1."""
    if categories is None or stages_completed is None:
        conn = _get_conn()
        story_id = row["id"]
        if categories is None:
            cats = conn.execute(
                "SELECT category FROM story_categories WHERE story_id = ?",
                (story_id,),
            ).fetchall()
            categories = [c["category"] for c in cats]
        if stages_completed is None:
            comps = conn.execute(
                "SELECT stage, completed_at FROM stage_completions WHERE story_id = ?",
                (story_id,),
            ).fetchall()
            stages_completed = {c["stage"]: c["completed_at"] for c in comps}

    keys = row.keys()
    return Story(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        title_furigana=row["title_furigana"] if "title_furigana" in keys else "",
        pub_date=row["pub_date"] or "",
        stage=row["stage"],
        error=row["error"],
        added_at=row["added_at"],
        updated_at=row["updated_at"],
        categories=categories,
        stages_completed=stages_completed,
        youtube_video_id=row["youtube_video_id"] if "youtube_video_id" in keys else None,
        content_type=row["content_type"] if "content_type" in keys else "long",
        author=row["author"] if "author" in keys else "",
        char_count=row["char_count"] if "char_count" in keys else None,
        source=(row["source"] if "source" in keys and row["source"] else "hhs"),
    )


def _rows_to_stories(rows: list[sqlite3.Row]) -> list[Story]:
    """Batch convert rows to Stories, loading relations in 2 queries instead of N*2."""
    if not rows:
        return []

    conn = _get_conn()
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))

    # Batch load categories
    cats_rows = conn.execute(
        f"SELECT story_id, category FROM story_categories WHERE story_id IN ({placeholders})",
        ids,
    ).fetchall()
    cats_by_id: dict[int, list[str]] = {}
    for c in cats_rows:
        cats_by_id.setdefault(c["story_id"], []).append(c["category"])

    # Batch load stage completions
    comp_rows = conn.execute(
        "SELECT story_id, stage, completed_at FROM stage_completions"
        f" WHERE story_id IN ({placeholders})",
        ids,
    ).fetchall()
    comps_by_id: dict[int, dict[str, str]] = {}
    for c in comp_rows:
        comps_by_id.setdefault(c["story_id"], {})[c["stage"]] = c["completed_at"]

    return [
        _row_to_story(r, cats_by_id.get(r["id"], []), comps_by_id.get(r["id"], {}))
        for r in rows
    ]


# ── CRUD ────────────────────────────────────────────


def add_story(
    url: str,
    title: str = "",
    title_furigana: str = "",
    pub_date: str = "",
    categories: list[str] | None = None,
    content_type: str = "long",
    author: str = "",
    char_count: int | None = None,
    source: str | None = None,
) -> Story | None:
    """Add a new story. Returns None if URL already exists."""
    conn = _get_conn()
    now = _now()
    src = source or infer_source_from_url(url)
    try:
        cur = conn.execute(
            "INSERT INTO stories (url, title, title_furigana, pub_date, stage, added_at, updated_at,"
            " content_type, author, char_count, source) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
            (url, title, title_furigana, pub_date, now, now, content_type, author, char_count, src),
        )
        story_id = cur.lastrowid
        for cat in categories or []:
            conn.execute(
                "INSERT OR IGNORE INTO story_categories (story_id, category) VALUES (?, ?)",
                (story_id, cat),
            )
        conn.commit()
        return get_story_by_id(story_id)
    except sqlite3.IntegrityError:
        conn.rollback()
        return None


def get_story_by_id(story_id: int) -> Story | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
    return _row_to_story(row) if row else None


def get_story_by_url(url: str) -> Story | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM stories WHERE url = ?", (url,)).fetchone()
    return _row_to_story(row) if row else None


def _build_story_filter(
    select: str,
    stage: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    content_type: str | None = None,
) -> tuple[str, list]:
    """Build a filtered query for stories. Returns (query, params)."""
    query = f"{select} FROM stories s"
    params: list = []

    if category:
        query += " JOIN story_categories sc ON s.id = sc.story_id"

    conditions = []
    if stage:
        conditions.append("s.stage = ?")
        params.append(stage)
    if category:
        conditions.append("sc.category = ?")
        params.append(category)
    if keyword:
        conditions.append("s.title LIKE ?")
        params.append(f"%{keyword}%")
    if content_type:
        conditions.append("s.content_type = ?")
        params.append(content_type)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    return query, params


def get_stories(
    stage: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
    content_type: str | None = None,
    order_by: str = "updated_at",
) -> list[Story]:
    """Query stories with optional filters.

    order_by: "updated_at" (default, most recently processed first) or "id"
    (insertion order). Both DESC.
    """
    conn = _get_conn()
    query, params = _build_story_filter(
        "SELECT DISTINCT s.*", stage=stage, category=category, keyword=keyword,
        content_type=content_type,
    )
    order_col = "s.updated_at" if order_by == "updated_at" else "s.id"
    query += f" ORDER BY {order_col} DESC, s.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    return _rows_to_stories(rows)


def count_stories(
    stage: str | None = None,
    category: str | None = None,
    content_type: str | None = None,
) -> int:
    conn = _get_conn()
    query, params = _build_story_filter(
        "SELECT COUNT(DISTINCT s.id)", stage=stage, category=category,
        content_type=content_type,
    )
    return conn.execute(query, params).fetchone()[0]


def get_stage_counts(content_type: str | None = None) -> dict[str, int]:
    """Get count of stories at each stage."""
    conn = _get_conn()
    if content_type:
        rows = conn.execute(
            "SELECT stage, COUNT(*) as cnt FROM stories WHERE content_type = ? GROUP BY stage",
            (content_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT stage, COUNT(*) as cnt FROM stories GROUP BY stage"
        ).fetchall()
    return {r["stage"]: r["cnt"] for r in rows}


def get_categories() -> list[str]:
    """Get all distinct categories."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT DISTINCT category FROM story_categories ORDER BY category"
    ).fetchall()
    return [r["category"] for r in rows]


def update_stage(story_id: int, stage: str, error: str | None = None) -> None:
    """Update story stage and optionally set error."""
    conn = _get_conn()
    now = _now()
    conn.execute(
        "UPDATE stories SET stage = ?, error = ?, updated_at = ? WHERE id = ?",
        (stage, error, now, story_id),
    )
    if error is None and stage in STAGES:
        conn.execute(
            "INSERT OR REPLACE INTO stage_completions (story_id, stage, completed_at) "
            "VALUES (?, ?, ?)",
            (story_id, stage, now),
        )
    conn.commit()


def update_char_count(story_id: int, char_count: int) -> None:
    """Update the character count for a story."""
    conn = _get_conn()
    conn.execute(
        "UPDATE stories SET char_count = ?, updated_at = ? WHERE id = ?",
        (char_count, _now(), story_id),
    )
    conn.commit()


def update_title_furigana(story_id: int, title_furigana: str) -> None:
    """Update the title furigana for a story."""
    conn = _get_conn()
    conn.execute(
        "UPDATE stories SET title_furigana = ?, updated_at = ? WHERE id = ?",
        (title_furigana, _now(), story_id),
    )
    conn.commit()


def set_youtube_video_id(story_id: int, video_id: str) -> None:
    """Store the YouTube video ID for a story."""
    conn = _get_conn()
    conn.execute(
        "UPDATE stories SET youtube_video_id = ?, updated_at = ? WHERE id = ?",
        (video_id, _now(), story_id),
    )
    conn.commit()


def reset_to_stage(story_id: int, target_stage: str) -> None:
    """Reset a story to a specific stage, clearing later completions."""
    conn = _get_conn()
    now = _now()
    idx = STAGES.index(target_stage)
    later_stages = STAGES[idx + 1 :]

    conn.execute(
        "UPDATE stories SET stage = ?, error = NULL, updated_at = ? WHERE id = ?",
        (target_stage, now, story_id),
    )
    if later_stages:
        placeholders = ",".join("?" * len(later_stages))
        conn.execute(
            f"DELETE FROM stage_completions WHERE story_id = ? AND stage IN ({placeholders})",
            [story_id, *later_stages],
        )
    conn.commit()


def delete_story(story_id: int) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM stories WHERE id = ?", (story_id,))
    conn.commit()


def convert_to_short(story_id: int) -> None:
    """Migrate a long-form story to the Shorts pipeline.

    Rewinds stage to 'text_processed' so the Shorts pipeline re-runs the
    voice stage. Long speed (0.9) and Shorts speed (1.15) differ, so the
    long narration cannot be reused — only the scraped/processed text is
    reusable. The `source` field is preserved.

    Deletes stage_completions beyond text_processed so the Shorts pipeline
    re-runs voice/image/video/upload/(report) stages on the new content_type.

    Copies reusable artifacts (raw_content/processed_text/chunks/original_chunks)
    from the long output dir to the short output dir, so the Shorts pipeline
    workers find them at the expected short paths without re-scraping /
    re-processing text. The long-side files are preserved intact for rollback.
    """
    conn = _get_conn()
    now = _now()
    target_stage = "text_processed"
    # STAGES_SHORT is now used for clearing completions
    from app.models import STAGES_SHORT
    idx = STAGES_SHORT.index(target_stage)
    later_stages = STAGES_SHORT[idx + 1:]

    # Fetch title before updating so we can copy files using the correct title
    story = get_story_by_id(story_id)
    if story is None:
        raise ValueError(f"Story {story_id} not found")

    conn.execute(
        "UPDATE stories SET content_type = 'short', stage = ?, error = NULL, "
        "youtube_video_id = NULL, updated_at = ? WHERE id = ?",
        (target_stage, now, story_id),
    )
    if later_stages:
        placeholders = ",".join("?" * len(later_stages))
        conn.execute(
            f"DELETE FROM stage_completions WHERE story_id = ? AND stage IN ({placeholders})",
            [story_id, *later_stages],
        )
    conn.commit()

    # Copy reusable artifacts to the short directory so the Shorts pipeline
    # can reuse them without re-running scraping/text-processing/voice stages.
    _copy_long_artifacts_to_short(story.title)


def _copy_long_artifacts_to_short(title: str) -> None:
    """Copy scraped text + processed hiragana artifacts from long dir to short dir.

    Narration and per-chunk audio are intentionally NOT copied — long uses
    speed=0.9 but Shorts uses speed=1.15, so voice must be regenerated.

    Missing files are silently skipped (e.g. stories that haven't reached
    text_processed won't have processed_text.txt / chunks.json).
    """
    _copy_text_artifacts(title, src_ct="long", dst_ct="short")


def _copy_text_artifacts(title: str, src_ct: str, dst_ct: str) -> None:
    """Copy scraped/processed text artifacts between content_type directories.

    Copies: raw_content, processed_text, chunks, original_chunks.
    Does NOT copy narration/audio (voice speeds differ between long and short).
    Missing files are silently skipped.
    """
    import shutil

    from app.utils.paths import (
        chunks_path,
        original_chunks_path,
        processed_text_path,
        raw_content_path,
        story_dir,
    )

    story_dir(title, dst_ct)  # ensures dst dir exists

    for path_fn in (
        raw_content_path,
        processed_text_path,
        chunks_path,
        original_chunks_path,
    ):
        src = path_fn(title, src_ct)
        if src.exists():
            dst = path_fn(title, dst_ct)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def convert_to_long(story_id: int) -> None:
    """Migrate a Shorts story to the long-form pipeline.

    Rewinds stage to 'text_processed' so the long pipeline regenerates voice
    (short speed=1.15 ≠ long speed=0.9 — audio must be re-synthesized).

    The `source` field is preserved so kikikaikai-sourced shorts keep their
    kikikaikai attribution in the long pipeline (upload stage branches on
    source for the correct 引用元 template).

    Copies reusable text artifacts from short dir → long dir. Short-side
    files are preserved for rollback.
    """
    conn = _get_conn()
    now = _now()
    target_stage = "text_processed"
    idx = STAGES.index(target_stage)
    later_stages = STAGES[idx + 1:]

    story = get_story_by_id(story_id)
    if story is None:
        raise ValueError(f"Story {story_id} not found")

    conn.execute(
        "UPDATE stories SET content_type = 'long', stage = ?, error = NULL, "
        "youtube_video_id = NULL, updated_at = ? WHERE id = ?",
        (target_stage, now, story_id),
    )
    if later_stages:
        placeholders = ",".join("?" * len(later_stages))
        conn.execute(
            f"DELETE FROM stage_completions WHERE story_id = ? AND stage IN ({placeholders})",
            [story_id, *later_stages],
        )
    conn.commit()

    _copy_text_artifacts(story.title, src_ct="short", dst_ct="long")


def recover_running() -> int:
    """Reset stories stuck in :running state back to their input stage."""
    conn = _get_conn()
    now = _now()
    rows = conn.execute(
        "SELECT id, stage, content_type FROM stories WHERE stage LIKE '%:running'"
    ).fetchall()
    keys = rows[0].keys() if rows else []
    count = 0
    for row in rows:
        base_stage = row["stage"].replace(":running", "")
        ct = row["content_type"] if "content_type" in keys else "long"
        stage_list = stages_for(ct)
        prev = stage_list[stage_list.index(base_stage) - 1] if stage_list.index(base_stage) > 0 else "pending"
        conn.execute(
            "UPDATE stories SET stage = ?, error = NULL, updated_at = ? WHERE id = ?",
            (prev, now, row["id"]),
        )
        count += 1
    conn.commit()
    return count


def get_stories_at_stage(
    stage: str, limit: int = 1, content_type: str | None = None,
) -> list[Story]:
    """Get stories ready for processing at a given stage (input stage for workers).

    Stories with an error set are skipped to prevent infinite retry loops.
    Users must clear the error (e.g., via UI retry button) to re-queue.
    """
    conn = _get_conn()
    # Prioritize by char_count ASC (shorter stories first), fallback to id ASC
    order = "ORDER BY COALESCE(char_count, 999999) ASC, id ASC"
    if content_type:
        rows = conn.execute(
            f"SELECT * FROM stories WHERE stage = ? AND content_type = ? "
            f"AND (error IS NULL OR error = '') {order} LIMIT ?",
            (stage, content_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM stories WHERE stage = ? "
            f"AND (error IS NULL OR error = '') {order} LIMIT ?",
            (stage, limit),
        ).fetchall()
    return _rows_to_stories(rows)


def mark_running(story_id: int, stage: str) -> None:
    """Mark a story as running for a given stage. Clears any previous error."""
    conn = _get_conn()
    conn.execute(
        "UPDATE stories SET stage = ?, error = NULL, updated_at = ? WHERE id = ?",
        (f"{stage}:running", _now(), story_id),
    )
    conn.commit()


def mark_failed(story_id: int, stage: str, error: str, content_type: str = "long") -> None:
    """Mark a story as failed without resetting stage.

    The story keeps its current stage but gets an error flag.
    get_stories_at_stage() skips stories with errors, preventing
    infinite retry loops. Users can clear the error via UI to retry.
    """
    conn = _get_conn()
    stage_list = stages_for(content_type)
    prev = stage_list[stage_list.index(stage) - 1] if stage_list.index(stage) > 0 else "pending"
    conn.execute(
        "UPDATE stories SET stage = ?, error = ?, updated_at = ? WHERE id = ?",
        (prev, error, _now(), story_id),
    )
    conn.commit()


# ── Logging ─────────────────────────────────────────


def add_log(
    level: str, message: str, stage: str | None = None, story_id: int | None = None
) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO logs (timestamp, level, stage, story_id, message) VALUES (?, ?, ?, ?, ?)",
        (_now(), level, stage, story_id, message),
    )
    conn.commit()


def get_logs(
    story_id: int | None = None,
    stage: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conn = _get_conn()
    query = "SELECT * FROM logs"
    params: list = []
    conditions = []

    if story_id:
        conditions.append("story_id = ?")
        params.append(story_id)
    if stage:
        conditions.append("stage = ?")
        params.append(stage)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
