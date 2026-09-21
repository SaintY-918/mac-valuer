"""Revisiting stored listings on their own pages.

Every scraper reads a window of the newest listings, so a row is seen once and
then scrolls out. Before this, nothing looked at it again: on 2026-09-21, 22 of
86 Carousell rows and 5 of 17 PTT rows marked available were 404 or 410.

No network access: requests.get is replaced with canned responses.
"""

import asyncio
import concurrent.futures
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.database.db_manager import DBManager, Deal
from src.notifier.discord_notify import _heartbeat_content
from src.scrapers.carousell import CarousellScraper
from src.scrapers.ptt import PTTScraper
from src.scrapers.shopee import ShopeeScraper


def _run(coro):
    # Same reason as test_scraper_filters._run: asyncio.run() cannot nest
    # inside the loop the browser tests keep open.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _resp(status: int, text: str = ""):
    def raise_for_status():
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
    return SimpleNamespace(status_code=status, text=text, encoding=None,
                           raise_for_status=raise_for_status)


# ── What each scraper concludes from the page ────────────────────────────────

_PTT_PAGE = """<div id="main-content"><div class="article-metaline">
<span class="article-meta-value">[販售] 台北 MacBook Air M3 {title_tail}</span></div>
售價：32000
{body}
--
※ 發信站: 批踢踢實業坊(ptt.cc)</div>"""


@pytest.mark.parametrize("status,page,expected", [
    (404, "", "gone"),
    (200, _PTT_PAGE.format(title_tail="", body="功能正常"), "available"),
    (200, _PTT_PAGE.format(title_tail="", body="已售出，謝謝"), "sold"),
    # Sellers edit the title rather than the body just as often.
    (200, _PTT_PAGE.format(title_tail="(已售出)", body="功能正常"), "sold"),
    (200, "<html>maintenance</html>", None),
])
def test_ptt_reads_the_article_page(monkeypatch, status, page, expected):
    monkeypatch.setattr("src.scrapers.ptt.requests.get", lambda *a, **k: _resp(status, page))
    assert PTTScraper().check_listing("https://www.ptt.cc/bbs/MacShop/M.1.A.2.html") == expected


def test_ptt_does_not_call_a_server_error_gone(monkeypatch):
    """Only a 404 means the post is gone. A 503 means nothing about it."""
    monkeypatch.setattr("src.scrapers.ptt.requests.get", lambda *a, **k: _resp(503))
    with pytest.raises(RuntimeError):
        PTTScraper().check_listing("https://www.ptt.cc/bbs/MacShop/M.1.A.2.html")


def _carousell_page(availability: str, description: str = "功能正常") -> str:
    product = {"@type": "Product", "name": "MacBook Air M2", "description": description,
               "offers": {"price": "25000", "availability": f"https://schema.org/{availability}"}}
    return f'<script type="application/ld+json">{json.dumps(product)}</script>'


@pytest.mark.parametrize("status,page,expected", [
    (410, "", "gone"),
    (404, "", "gone"),
    (200, _carousell_page("InStock"), "available"),
    (200, _carousell_page("OutOfStock"), "sold"),
    (200, _carousell_page("InStock", "已售出"), "sold"),
    (200, "<html>no product block</html>", None),
])
def test_carousell_reads_the_product_page(monkeypatch, status, page, expected):
    monkeypatch.setattr("src.scrapers.carousell.requests.get", lambda *a, **k: _resp(status, page))
    assert CarousellScraper().check_listing("https://tw.carousell.com/p/x-1/") == expected


def test_carousell_does_not_call_a_block_gone(monkeypatch):
    """A 403 is Carousell refusing the caller, as it does every GitHub runner."""
    monkeypatch.setattr("src.scrapers.carousell.requests.get", lambda *a, **k: _resp(403))
    with pytest.raises(RuntimeError):
        CarousellScraper().check_listing("https://tw.carousell.com/p/x-1/")


def test_revisit_turns_a_failure_into_unknown(monkeypatch):
    """One bad page must not sink the batch, and must not count as gone."""
    monkeypatch.setenv("CAROUSELL_DELAY", "0")
    scraper = CarousellScraper()
    pages = {"a": _resp(410), "b": _resp(403), "c": _resp(200, _carousell_page("InStock"))}
    monkeypatch.setattr(scraper, "_response", lambda url: pages[url])
    assert _run(scraper.revisit(["a", "b", "c"])) == {"a": "gone", "b": None, "c": "available"}


def test_shopee_does_not_claim_to_revisit():
    """Every Shopee product URL returns the same 204 KB JavaScript shell over
    plain HTTP, so there is nothing to read."""
    assert ShopeeScraper.REVISITS is False
    assert PTTScraper.REVISITS and CarousellScraper.REVISITS


# ── What the database does with the outcome ──────────────────────────────────

@pytest.fixture
def db():
    tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db".replace("\\", "/")
    yield DBManager()


def _add(db, url, status="available", days_ago=5, source="carousell"):
    then = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)
    with db.Session() as s:
        s.add(Deal(url=url, source=source, status=status, title=url, body_content="",
                   first_seen=then, updated_at=then, last_seen=then))
        s.commit()


def _row(db, url):
    with db.Session() as s:
        d = s.get(Deal, url)
        return d.status, d.last_seen


def test_gone_retires_without_claiming_a_sighting(db):
    _add(db, "u")
    before = _row(db, "u")[1]
    assert db.record_revisit("u", "gone")
    assert _row(db, "u") == ("unavailable", before)


def test_available_is_a_sighting(db):
    _add(db, "u", days_ago=13)
    db.record_revisit("u", "available")
    status, seen = _row(db, "u")
    assert status == "available"
    assert seen > datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    # The whole point: a live listing seen again no longer ages out on day 14.
    assert db.sweep_stale("carousell", max_age_days=14) == 0


def test_sold_is_recorded(db):
    _add(db, "u")
    db.record_revisit("u", "sold")
    assert _row(db, "u")[0] == "sold"


def test_a_row_deleted_meanwhile_is_not_an_error(db):
    assert db.record_revisit("missing", "gone") is False


def test_nonsense_outcomes_are_refused(db):
    _add(db, "u")
    with pytest.raises(ValueError):
        db.record_revisit("u", "unavailable")


def test_available_urls_puts_the_longest_unseen_first(db):
    _add(db, "recent", days_ago=1)
    _add(db, "old", days_ago=10)
    _add(db, "sold", status="sold", days_ago=20)
    _add(db, "other", days_ago=30, source="ptt")
    assert db.available_urls("carousell") == ["old", "recent"]


# ── Heartbeat ────────────────────────────────────────────────────────────────

def test_heartbeat_reports_what_the_revisit_found():
    text = _heartbeat_content({
        "counts": {"carousell": 3}, "errors": {}, "alerts_sent": 0,
        "revisits": {"carousell": {"gone": 22, "sold": 2, "available": 62, "unknown": 0}},
    })
    assert "下架 **22**" in text and "售出 **2**" in text and "無法判斷 0" in text
