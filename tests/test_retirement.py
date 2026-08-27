"""Retirement of listings that stopped appearing.

Every scraper here reads a rolling window, not full inventory: PTT an Atom feed
of recent posts, Shopee the newest ~180 search results, Carousell the newest
entries in a sitemap. "Absent from this run" therefore means the listing
scrolled out of the window, not that it sold — two Shopee runs once returned
completely disjoint sets (35 products vs 30, zero overlap), and the set-based
sweep marked 46 live listings unavailable in one go.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db():
    """A throwaway SQLite database. Tests must never touch the real one."""
    tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db".replace("\\", "/")
    from src.database.db_manager import DBManager
    yield DBManager()


def _add(db, url, source, status, days_ago):
    from src.database.db_manager import Deal
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.Session() as s:
        s.add(Deal(url=url, source=source, status=status, title=url,
                   body_content="", parsed_json=None, first_seen=now,
                   updated_at=now, last_seen=now - timedelta(days=days_ago)))
        s.commit()


def _status(db, url):
    from src.database.db_manager import Deal
    with db.Session() as s:
        return s.get(Deal, url).status


def test_retires_only_what_has_been_gone_long_enough(db):
    _add(db, "fresh", "shopee", "available", 0)
    _add(db, "inside", "shopee", "available", 13)
    _add(db, "outside", "shopee", "available", 15)

    assert db.sweep_stale("shopee", max_age_days=14) == 1
    assert _status(db, "fresh") == "available"
    assert _status(db, "inside") == "available"
    assert _status(db, "outside") == "unavailable"


def test_leaves_other_sources_alone(db):
    _add(db, "old_ptt", "ptt", "available", 90)
    db.sweep_stale("shopee", max_age_days=14)
    assert _status(db, "old_ptt") == "available"


def test_does_not_disturb_settled_statuses(db):
    _add(db, "sold", "shopee", "sold", 90)
    _add(db, "gone", "shopee", "unavailable", 90)
    db.sweep_stale("shopee", max_age_days=14)
    assert _status(db, "sold") == "sold"
    assert _status(db, "gone") == "unavailable"


def test_running_it_twice_changes_nothing_the_second_time(db):
    _add(db, "old", "shopee", "available", 90)
    assert db.sweep_stale("shopee", max_age_days=14) == 1
    assert db.sweep_stale("shopee", max_age_days=14) == 0


def test_a_listing_seen_today_is_never_retired(db):
    """The bug this replaces retired anything missing from the current run,
    regardless of how recently it had been seen."""
    for i in range(5):
        _add(db, f"seen_{i}", "shopee", "available", 0)
    assert db.sweep_stale("shopee", max_age_days=14) == 0


def test_source_returns_only_its_own_rows(db):
    """get_filtered_deals filters on source in SQL but once omitted it from the
    returned dict, so the dashboard could not tell listings apart."""
    _add(db, "a", "carousell", "available", 0)
    rows = db.get_filtered_deals(status="available", source="carousell")
    assert all(r.get("source") == "carousell" for r in rows)
