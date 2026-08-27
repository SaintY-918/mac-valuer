"""What each scraper lets through, and what it drops.

These filters are the only thing standing between the pipeline and other
people's junk. Live data has produced a Honor laptop advertised as
"鋁合金類macbook", a listing titled "888" with a 99999 placeholder price, and
Shopee search rows priced in micro-units.

No network access here: the scrapers' per-item builders are pure functions over
the payload each platform returns.
"""

import re

import pytest

from src.scrapers.carousell import CarousellScraper
from src.scrapers.shopee import ShopeeScraper

PRICE_RE = re.compile(r"售價為 (\d+) 元")


# ── Shopee: building a listing from the search response alone ─────────────────

@pytest.fixture
def shopee(monkeypatch):
    monkeypatch.delenv("SHOPEE_APP_ID", raising=False)
    monkeypatch.delenv("SHOPEE_APP_SECRET", raising=False)
    return ShopeeScraper()


def _item(**kw):
    base = {"shopid": 1, "itemid": 2, "name": "二手 Macbook Air M4 16G/256G",
            "price": 2350000000, "stock": 3}
    base.update(kw)
    return base


def test_builds_a_listing_from_a_search_row(shopee):
    listing = shopee._listing_from_search_item(_item(shop_location="台北市"))
    assert listing.source == "shopee"
    assert listing.url == "https://shopee.tw/product/1/2"
    assert "台北市" in listing.body_content


def test_price_survives_the_regex_main_depends_on(shopee):
    """main.py reads the price back out of body_content with this pattern; if
    the annotation format drifts, every price silently becomes unknown."""
    listing = shopee._listing_from_search_item(_item(price=2350000000))
    assert PRICE_RE.search(listing.body_content).group(1) == "23500"


@pytest.mark.parametrize("item", [
    _item(stock=0),                       # L2: sold out
    _item(price=0),                       # no price
    _item(price=99000000),                # 990 TWD, below the L1 floor
    {"shopid": 1, "name": "x", "price": 2350000000},   # no itemid
    _item(name=""),                       # no title
])
def test_drops_what_should_not_reach_the_pipeline(shopee, item):
    assert shopee._listing_from_search_item(item) is None


def test_missing_stock_is_unknown_not_sold_out(shopee):
    """The field is absent on some rows. Treating absence as zero would discard
    live listings."""
    item = _item()
    del item["stock"]
    assert shopee._listing_from_search_item(item) is not None


def test_body_stays_within_the_l3_cap(shopee):
    listing = shopee._listing_from_search_item(_item(name="長標題" * 400))
    assert len(listing.body_content) <= 800


# ── Carousell: what the JSON-LD product block is allowed to become ────────────

@pytest.fixture
def carousell():
    return CarousellScraper()


def _product(**kw):
    base = {"name": "二手 MacBook Air M4 16G/512G",
            "description": "使用一年，外觀良好",
            "offers": {"price": "32000.00",
                       "availability": "https://schema.org/InStock"}}
    base.update(kw)
    return base


def _listing(scraper, product, monkeypatch, url="https://tw.carousell.com/p/x-1/"):
    monkeypatch.setattr(scraper, "_get", lambda _u: "")
    monkeypatch.setattr(scraper, "_product_jsonld", staticmethod(lambda _h: product))
    return scraper._fetch_one(url)


def test_accepts_a_genuine_listing(carousell, monkeypatch):
    listing = _listing(carousell, _product(), monkeypatch)
    assert listing.source == "carousell"
    assert listing.status == "available"
    assert PRICE_RE.search(listing.body_content).group(1) == "32000"


def test_rejects_a_windows_laptop_calling_itself_macbook_like(carousell, monkeypatch):
    """A Honor laptop reached the pipeline because its URL slug said
    "鋁合金類macbook". The model name has to be in the title itself."""
    product = _product(name="AMD Ryzen 14吋筆電 / R7 5700U / SSD 1TB / RAM 8G")
    assert _listing(carousell, product, monkeypatch) is None


def test_rejects_a_placeholder_listing(carousell, monkeypatch):
    """One seller's title was literally "888", with 99999 as a do-not-lowball
    price. The same title rule catches it."""
    product = _product(name="888", offers={"price": "99999.00",
                                           "availability": "https://schema.org/InStock"})
    assert _listing(carousell, product, monkeypatch) is None


def test_marks_sold_out_listings_sold(carousell, monkeypatch):
    product = _product(offers={"price": "32000.00",
                               "availability": "https://schema.org/OutOfStock"})
    assert _listing(carousell, product, monkeypatch).status == "sold"


@pytest.mark.parametrize("price", ["1000.00", "500000.00"])
def test_enforces_the_price_range(carousell, monkeypatch, price):
    product = _product(offers={"price": price,
                               "availability": "https://schema.org/InStock"})
    assert _listing(carousell, product, monkeypatch) is None


def test_a_page_without_the_product_block_is_skipped_not_fatal(carousell, monkeypatch):
    assert _listing(carousell, None, monkeypatch) is None
