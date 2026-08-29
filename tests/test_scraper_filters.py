"""What each scraper lets through, and what it drops.

These filters are the only thing standing between the pipeline and other
people's junk. Live data has produced a Honor laptop advertised as
"鋁合金類macbook", a listing titled "888" with a 99999 placeholder price, and
Shopee search rows priced in micro-units.

No network access here: the scrapers' per-item builders are pure functions over
the payload each platform returns.
"""

import asyncio
import concurrent.futures
import re
from types import SimpleNamespace

import pytest

from src.scrapers.carousell import CarousellScraper
from src.scrapers.ptt import PTTScraper, _main_content_text
from src.scrapers.shopee import ShopeeScraper

PRICE_RE = re.compile(r"售價為 (\d+) 元")


def _run(coro):
    """Drive a coroutine to completion from a sync test.

    Not asyncio.run(): the browser tests hold Playwright's sync API open for the
    whole session, which keeps an event loop running in this thread, and
    asyncio.run() refuses to nest inside one. A worker thread has no loop of its
    own. The failure only appears in a full-suite run, never in this file alone.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


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


# ── PTT: reading an article without a browser ─────────────────────────────────

# Trimmed from a real MacShop article. The nesting matters: main-content holds
# metaline divs and push divs, which is why the extractor cannot just match to
# the first closing tag.
_ARTICLE = """<html><body><div id="main-container">
<div id="main-content" class="bbs-screen bbs-content">
<div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">seller</span></div>
<div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[賣機] MacBook Air M3</span></div>
[讓售] MacBook Air M3 15寸<br>售價 &lt;&lt; 32000 &gt;&gt;<br>
--<br>※ 發信站: 批踢踢實業坊
<div class="push"><span class="f3 push-content">: 輸入法</span></div>
</div></div><script>var x = "</div>";</script></body></html>"""


def test_reads_the_article_body_out_of_the_markup():
    text = _main_content_text(_ARTICLE)
    assert "MacBook Air M3 15寸" in text
    assert "<span" not in text and "<div" not in text


def test_unescapes_entities_so_the_price_regex_can_see_the_number():
    """A price written as &lt;&lt; 32000 &gt;&gt; has to survive as << 32000 >>."""
    assert "<< 32000 >>" in _main_content_text(_ARTICLE)


def test_script_contents_never_reach_the_body():
    """A </div> inside a <script> string would end the body early if trusted."""
    assert "var x" not in _main_content_text(_ARTICLE)


def test_a_page_without_main_content_yields_nothing_rather_than_garbage():
    assert _main_content_text("<html><body>nope</body></html>") == ""


def test_the_signature_separator_still_cuts_the_body(monkeypatch):
    scraper = PTTScraper()
    monkeypatch.setattr(scraper, "_get", lambda url: _ARTICLE)
    body = _run(scraper._body_text("https://www.ptt.cc/bbs/MacShop/M.1.A.2.html"))
    assert "MacBook Air M3" in body
    assert "發信站" not in body


def test_an_unreachable_feed_raises_instead_of_looking_like_a_quiet_day(monkeypatch):
    """feedparser reports a network failure as an object with no entries.

    Returning [] there would reach the heartbeat as "0 筆" — indistinguishable
    from a genuinely quiet board, which is the confusion decisions #21 fixed
    everywhere else.
    """
    dead = SimpleNamespace(entries=[], bozo=1, bozo_exception="connection refused")
    monkeypatch.setattr("src.scrapers.ptt.feedparser.parse", lambda url: dead)
    with pytest.raises(RuntimeError, match="no entries"):
        _run(PTTScraper().fetch_listings())


def test_the_board_filter_no_longer_stops_at_m4(monkeypatch):
    """_M_CHIPS was ["m1","m2","m3","m4"], so M5 and A-series never got fetched.

    main.py had already been fixed for this twice — once when the extractor's
    list stopped at M4 and lost nine listings, once when MacBook Neo's A18 Pro
    was discarded whole. This was the same mistake's third copy, in the one
    place that decides whether a listing is fetched at all.
    """
    entries = [
        SimpleNamespace(title="[賣機] MacBook Pro M5 Max 16吋", link="https://p/m5"),
        SimpleNamespace(title="[賣機] MacBook Neo A18 Pro 8G/256G", link="https://p/a18"),
        SimpleNamespace(title="[賣機] MacBook Air M2 13吋", link="https://p/m2"),
        SimpleNamespace(title="[徵求] MacBook Air M3", link="https://p/wanted"),
        SimpleNamespace(title="[賣機] MacBook Pro 2016 Core m5 1.2G", link="https://p/intel"),
    ]
    monkeypatch.setattr("src.scrapers.ptt.feedparser.parse",
                        lambda url: SimpleNamespace(entries=entries))

    scraper = PTTScraper()
    monkeypatch.setattr(scraper, "_delay", 0)
    fetched = []

    def _fake_get(url):
        fetched.append(url)
        return _ARTICLE

    monkeypatch.setattr(scraper, "_get", _fake_get)
    _run(scraper.fetch_listings())

    assert "https://p/m5" in fetched
    assert "https://p/a18" in fetched
    assert "https://p/m2" in fetched
    # 徵求 is someone buying, and a Core m5 is an Intel machine this project
    # cannot score — both still dropped.
    assert "https://p/wanted" not in fetched
    assert "https://p/intel" not in fetched
