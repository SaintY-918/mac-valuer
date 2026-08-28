"""Retirement of listings that stopped appearing.

Every scraper here reads a rolling window, not full inventory: PTT an Atom feed
of recent posts, Shopee the newest ~180 search results, Carousell the newest
entries in a sitemap. "Absent from this run" therefore means the listing
scrolled out of the window, not that it sold — two Shopee runs once returned
completely disjoint sets (35 products vs 30, zero overlap), and the set-based
sweep marked 46 live listings unavailable in one go.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from src.database.db_manager import DBManager


@pytest.fixture
def db():
    """A throwaway SQLite database. Tests must never touch the real one."""
    tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db".replace("\\", "/")
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


# ── model_type filter ────────────────────────────────────────────────────────
# The Neo is neither an Air nor a Pro. It was absent from both the enum and the
# filter, so a listing for one could not be described or found.

def _seed(tmp_path, rows):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.database.db_manager import Base, Deal

    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        for i, series in enumerate(rows):
            s.add(Deal(url=f"u{i}", source="ptt", title=f"t{i}", status="available",
                       parsed_json=json.dumps({"series": series, "price": 30000})))
        s.commit()
    engine.dispose()
    os.environ["DATABASE_URL"] = url
    return DBManager()


@pytest.mark.parametrize("model_type,expected", [
    ("Air", 1),
    ("Pro", 2),      # Pro 13 and Pro 14/16 both count
    ("Neo", 1),
    (None, 4),       # no filter
])
def test_model_type_filter_covers_every_family(tmp_path, model_type, expected):
    db = _seed(tmp_path, ["Air", "Pro 13", "Pro 14/16", "Neo"])
    assert len(db.get_filtered_deals(status="available", model_type=model_type)) == expected


def test_every_series_the_parser_can_produce_is_filterable(tmp_path):
    """The enum and the filter map have to stay in step; a family in one and
    not the other is invisible rather than broken, which is worse."""
    from src.database.db_manager import _MODEL_TYPE_SERIES
    from src.models.mac_spec import ModelSeries
    covered = {s for group in _MODEL_TYPE_SERIES.values() for s in group}
    assert {m.value for m in ModelSeries} == covered
