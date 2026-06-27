"""Tests for the startup source-backfill migration.

Background: the old migration blindly set source='kikikaikai' for every
Shorts row, which destroyed the correct source on HHS→Short conversions
(every container restart overwrote it). The fix makes the migration
URL-based and idempotent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app import database as db


@pytest.fixture
def fresh_db(tmp_path):
    test_db = tmp_path / "test.db"
    with patch.object(db, "DB_PATH", test_db):
        if hasattr(db._local, "conn"):
            db._local.conn = None
        db.init_db()
        yield db._get_conn()
        if hasattr(db._local, "conn") and db._local.conn:
            db._local.conn.close()
            db._local.conn = None


def _insert(conn, *, url, content_type, source):
    conn.execute(
        "INSERT INTO stories (url, title, stage, added_at, updated_at,"
        " content_type, source) VALUES (?, '題', 'pending', 't', 't', ?, ?)",
        (url, content_type, source),
    )
    conn.commit()


def _run_migration(conn):
    """Run only the URL-based correction (mimics what init_db does at startup)."""
    conn.execute(
        "UPDATE stories SET source = 'kikikaikai' "
        "WHERE url LIKE '%kikikaikai%' AND source != 'kikikaikai'"
    )
    conn.execute(
        "UPDATE stories SET source = 'hhs' "
        "WHERE url LIKE '%hhs.parasite.jp%' AND source != 'hhs'"
    )
    conn.commit()


def _source_of(conn, url):
    return conn.execute("SELECT source FROM stories WHERE url = ?", (url,)).fetchone()[0]


def test_hhs_short_source_not_overwritten(fresh_db):
    """The core bug: an HHS-URL Short must keep source='hhs' across restarts."""
    url = "https://hhs.parasite.jp/hhslibrary/?p=6533"
    _insert(fresh_db, url=url, content_type="short", source="hhs")
    _run_migration(fresh_db)
    _run_migration(fresh_db)  # idempotent: second run shouldn't change anything
    assert _source_of(fresh_db, url) == "hhs"


def test_kikikaikai_short_stays_kikikaikai(fresh_db):
    """kikikaikai-URL Short with correct source should remain unchanged."""
    url = "https://kikikaikai.kusuguru.co.jp/38742"
    _insert(fresh_db, url=url, content_type="short", source="kikikaikai")
    _run_migration(fresh_db)
    assert _source_of(fresh_db, url) == "kikikaikai"


def test_misclassified_hhs_short_corrected(fresh_db):
    """A Short with HHS URL but wrong source='kikikaikai' should be corrected."""
    url = "https://hhs.parasite.jp/hhslibrary/?p=6677"
    _insert(fresh_db, url=url, content_type="short", source="kikikaikai")
    _run_migration(fresh_db)
    assert _source_of(fresh_db, url) == "hhs"


def test_misclassified_kikikaikai_corrected(fresh_db):
    """A row with kikikaikai URL but wrong source='hhs' should be corrected."""
    url = "https://kikikaikai.kusuguru.co.jp/99999"
    _insert(fresh_db, url=url, content_type="long", source="hhs")
    _run_migration(fresh_db)
    assert _source_of(fresh_db, url) == "kikikaikai"


def test_hhs_long_unchanged(fresh_db):
    """Existing HHS long stories should not be touched."""
    url = "https://hhs.parasite.jp/hhslibrary/?p=6270"
    _insert(fresh_db, url=url, content_type="long", source="hhs")
    _run_migration(fresh_db)
    assert _source_of(fresh_db, url) == "hhs"


def test_init_db_idempotent_for_hhs_short(fresh_db):
    """Full init_db() run twice must not corrupt an HHS Short's source.
    This is the end-to-end regression test for the original bug."""
    url = "https://hhs.parasite.jp/hhslibrary/?p=6533"
    _insert(fresh_db, url=url, content_type="short", source="hhs")
    db.init_db()  # simulate a container restart
    db.init_db()  # simulate another restart
    assert _source_of(fresh_db, url) == "hhs"
