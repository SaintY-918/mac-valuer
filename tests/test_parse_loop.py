"""The parse step's cost and its effect on last_seen.

Step 2 re-reads text already in the database and asks Gemini to fill the gaps.
Three properties matter and none of them were being held:

  - it must not claim the listing was seen, or sweep_stale can never retire it
  - it must not re-ask a question whose input has not changed
  - it must not grow without bound, because it scales with the size of the
    database rather than with how many new listings appeared

These test the pieces that can be tested without a key or a network: the
hash, the freshness rule, and the narrow database update.
"""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.db_manager import Base, DBManager, Deal
from src.main import _parse_input_hash

# ── the fingerprint ───────────────────────────────────────────────────────────

def test_same_text_gives_the_same_hash():
    assert _parse_input_hash("t", "b") == _parse_input_hash("t", "b")


def test_a_changed_body_changes_the_hash():
    """A rescrape that rewrites the listing must earn a fresh attempt."""
    assert _parse_input_hash("t", "b") != _parse_input_hash("t", "b2")


def test_a_changed_title_changes_the_hash():
    assert _parse_input_hash("t", "b") != _parse_input_hash("t2", "b")


def test_the_field_boundary_cannot_be_forged():
    """"ab" + "" and "a" + "b" are different listings and must not collide."""
    assert _parse_input_hash("ab", "") != _parse_input_hash("a", "b")


def test_missing_text_is_handled():
    """body_content is nullable; hashing must not raise on it."""
    assert _parse_input_hash("t", None) == _parse_input_hash("t", None)
    assert _parse_input_hash(None, None)


# ── update_parsed ─────────────────────────────────────────────────────────────

def _db(tmp_path) -> DBManager:
    """A DBManager on a throwaway SQLite file."""
    url = f"sqlite:///{(tmp_path / 'x.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        s.add(Deal(url="u1", source="ptt", title="t", body_content="b",
                   parsed_json=json.dumps({"chip": "M1"}), status="available"))
        s.commit()
    engine.dispose()

    import os
    os.environ["DATABASE_URL"] = url
    return DBManager()


def test_update_parsed_does_not_move_last_seen(tmp_path):
    """The whole point. last_seen means "seen on the platform", and a parse
    that re-reads stored text is not evidence of that."""
    db = _db(tmp_path)
    with db.Session() as s:
        before = s.get(Deal, "u1").last_seen

    assert db.update_parsed("u1", {"chip": "M2", "price": 30000})

    with db.Session() as s:
        row = s.get(Deal, "u1")
        assert row.last_seen == before
        assert json.loads(row.parsed_json)["chip"] == "M2"


def test_save_deal_does_move_last_seen(tmp_path):
    """The contrast that gives the rule its meaning."""
    db = _db(tmp_path)
    with db.Session() as s:
        before = s.get(Deal, "u1").last_seen

    db.save_deal("u1", "t", "b", {"chip": "M2", "price": 1}, source="ptt")

    with db.Session() as s:
        assert s.get(Deal, "u1").last_seen != before


def test_update_parsed_reports_an_unknown_url(tmp_path):
    """Returns False rather than raising: a row can be swept between reads."""
    assert _db(tmp_path).update_parsed("nope", {"chip": "M1"}) is False


def test_get_all_deals_reports_the_source(tmp_path):
    """Step 2 scopes itself by source; without this it scoped itself to nothing.

    This column has now been left out of a read path three times.
    """
    rows = _db(tmp_path).get_all_deals()
    assert rows and rows[0]["source"] == "ptt"


# ── the freshness rule, as the loop applies it ────────────────────────────────

def _should_reparse(parsed: dict | None, title: str, body: str) -> bool:
    """The condition Step 2 uses, isolated from the LLM call around it."""
    return not (parsed and parsed.get("parse_input_hash") == _parse_input_hash(title, body))


def test_an_unchanged_row_is_not_re_asked():
    parsed = {"parse_input_hash": _parse_input_hash("t", "b")}
    assert not _should_reparse(parsed, "t", "b")


def test_a_rescraped_row_is_asked_again():
    parsed = {"parse_input_hash": _parse_input_hash("t", "old body")}
    assert _should_reparse(parsed, "t", "new body")


def test_a_row_never_attempted_is_asked():
    assert _should_reparse({"chip": "M1"}, "t", "b")
    assert _should_reparse(None, "t", "b")
