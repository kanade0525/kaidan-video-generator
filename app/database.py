from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.models import STAGES, Story

DB_PATH = Path("data/kaidan.db")

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

        CREATE TABLE IF NOT EXISTS analytics_channel_daily (
            date TEXT PRIMARY KEY,
            views INTEGER DEFAULT 0,
            estimated_minutes_watched REAL DEFAULT 0,
            subscribers_gained INTEGER DEFAULT 0,
            subscribers_lost INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS analytics_video (
            video_id TEXT NOT NULL,
            date TEXT NOT NULL,
            views INTEGER DEFAULT 0,
            estimated_minutes_watched REAL DEFAULT 0,
            average_view_duration REAL DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            title TEXT DEFAULT '',
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (video_id, date)
        );
    """)
    # Add youtube_video_id column if missing (migration)
    try:
        conn.execute("ALTER TABLE stories ADD COLUMN youtube_video_id TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_story(row: sqlite3.Row) -> Story:
    conn = _get_conn()
    story_id = row["id"]

    cats = conn.execute(
        "SELECT category FROM story_categories WHERE story_id = ?", (story_id,)
    ).fetchall()

    completions = conn.execute(
        "SELECT stage, completed_at FROM stage_completions WHERE story_id = ?",
        (story_id,),
    ).fetchall()

    return Story(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        pub_date=row["pub_date"] or "",
        stage=row["stage"],
        error=row["error"],
        added_at=row["added_at"],
        updated_at=row["updated_at"],
        categories=[c["category"] for c in cats],
        stages_completed={c["stage"]: c["completed_at"] for c in completions},
        youtube_video_id=row["youtube_video_id"] if "youtube_video_id" in row.keys() else None,
    )


# ── CRUD ────────────────────────────────────────────


def add_story(
    url: str,
    title: str = "",
    pub_date: str = "",
    categories: list[str] | None = None,
) -> Story | None:
    """Add a new story. Returns None if URL already exists."""
    conn = _get_conn()
    now = _now()
    try:
        cur = conn.execute(
            "INSERT INTO stories (url, title, pub_date, stage, added_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (url, title, pub_date, now, now),
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


def get_stories(
    stage: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Story]:
    """Query stories with optional filters."""
    conn = _get_conn()
    query = "SELECT DISTINCT s.* FROM stories s"
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

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY s.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    return [_row_to_story(r) for r in rows]


def count_stories(stage: str | None = None, category: str | None = None) -> int:
    conn = _get_conn()
    query = "SELECT COUNT(DISTINCT s.id) FROM stories s"
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

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    return conn.execute(query, params).fetchone()[0]


def get_stage_counts() -> dict[str, int]:
    """Get count of stories at each stage."""
    conn = _get_conn()
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


def recover_running() -> int:
    """Reset stories stuck in :running state back to their input stage."""
    conn = _get_conn()
    now = _now()
    rows = conn.execute(
        "SELECT id, stage FROM stories WHERE stage LIKE '%:running'"
    ).fetchall()
    count = 0
    for row in rows:
        base_stage = row["stage"].replace(":running", "")
        prev = STAGES[STAGES.index(base_stage) - 1] if STAGES.index(base_stage) > 0 else "pending"
        conn.execute(
            "UPDATE stories SET stage = ?, error = NULL, updated_at = ? WHERE id = ?",
            (prev, now, row["id"]),
        )
        count += 1
    conn.commit()
    return count


def get_stories_at_stage(stage: str, limit: int = 1) -> list[Story]:
    """Get stories ready for processing at a given stage (input stage for workers)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM stories WHERE stage = ? ORDER BY id ASC LIMIT ?",
        (stage, limit),
    ).fetchall()
    return [_row_to_story(r) for r in rows]


def mark_running(story_id: int, stage: str) -> None:
    """Mark a story as running for a given stage."""
    conn = _get_conn()
    conn.execute(
        "UPDATE stories SET stage = ?, updated_at = ? WHERE id = ?",
        (f"{stage}:running", _now(), story_id),
    )
    conn.commit()


def mark_failed(story_id: int, stage: str, error: str) -> None:
    """Mark a story as failed."""
    conn = _get_conn()
    prev = STAGES[STAGES.index(stage) - 1] if STAGES.index(stage) > 0 else "pending"
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


# ── Analytics ──────────────────────────────────────


def upsert_channel_daily(records: list[dict]) -> None:
    """Insert or update daily channel analytics records."""
    conn = _get_conn()
    now = _now()
    for r in records:
        conn.execute(
            """INSERT OR REPLACE INTO analytics_channel_daily
               (date, views, estimated_minutes_watched,
                subscribers_gained, subscribers_lost, likes, comments, shares, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.get("day", ""),
                r.get("views", 0),
                r.get("estimatedMinutesWatched", 0),
                r.get("subscribersGained", 0),
                r.get("subscribersLost", 0),
                r.get("likes", 0),
                r.get("comments", 0),
                r.get("shares", 0),
                now,
            ),
        )
    conn.commit()


def get_channel_daily(days: int = 28) -> list[dict]:
    """Get recent channel daily analytics from cache."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM analytics_channel_daily ORDER BY date DESC LIMIT ?",
        (days,),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def upsert_video_analytics(records: list[dict], period_date: str = "") -> None:
    """Insert or update per-video analytics records."""
    conn = _get_conn()
    now = _now()
    for r in records:
        conn.execute(
            """INSERT OR REPLACE INTO analytics_video
               (video_id, date, views, estimated_minutes_watched,
                average_view_duration, likes, comments, shares, title, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.get("video", ""),
                period_date,
                r.get("views", 0),
                r.get("estimatedMinutesWatched", 0),
                r.get("averageViewDuration", 0),
                r.get("likes", 0),
                r.get("comments", 0),
                r.get("shares", 0),
                r.get("title", ""),
                now,
            ),
        )
    conn.commit()


def get_video_analytics_cached(period_date: str = "") -> list[dict]:
    """Get cached per-video analytics for a period."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM analytics_video WHERE date = ? ORDER BY views DESC",
        (period_date,),
    ).fetchall()
    return [dict(r) for r in rows]
