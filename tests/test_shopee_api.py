"""src/scrapers/shopee_api.py (the affiliate GraphQL path) had zero direct
tests, even though it is the path that goes live the moment Shopee approves
the API key -- .spec/specs/scraper/spec.md 3.X requires the L1/L2/L3 gates and
the graceful-shutdown limit, and docs/decisions.md #18 is a whole postmortem
about MAX_LLM_CALLS_PER_RUN being ignored in exactly this kind of scraper.

No network access: requests.post is stubbed with SimpleNamespace responses,
matching the style in tests/test_scraper_filters.py.
"""

import hashlib
import time
from types import SimpleNamespace

import pytest

from src.scrapers import shopee_api
from src.scrapers.shopee_api import (
    ShopeeAffiliateScraper,
    ShopeeAuthError,
    _normalise_price,
    credentials_configured,
)

# ── credentials_configured ──────────────────────────────────────────────────

def test_credentials_configured_requires_both_id_and_secret(monkeypatch):
    monkeypatch.setenv("SHOPEE_APP_ID", "abc")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "")
    assert credentials_configured() is False

    monkeypatch.setenv("SHOPEE_APP_SECRET", "xyz")
    assert credentials_configured() is True


def test_credentials_configured_false_when_unset(monkeypatch):
    monkeypatch.delenv("SHOPEE_APP_ID", raising=False)
    monkeypatch.delenv("SHOPEE_APP_SECRET", raising=False)
    assert credentials_configured() is False


# ── _normalise_price ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, 0.0),
    ("not a number", 0.0),
    ("35000", 35000.0),
    (35000, 35000.0),
    (3_500_000_000, 35000.0),  # legacy micro-units: value * 100000
])
def test_normalise_price(raw, expected):
    assert _normalise_price(raw) == expected


# ── signed request headers ──────────────────────────────────────────────────

@pytest.fixture
def scraper(monkeypatch):
    monkeypatch.setenv("SHOPEE_APP_ID", "test-app-id")
    monkeypatch.setenv("SHOPEE_APP_SECRET", "test-secret")
    return ShopeeAffiliateScraper()


def test_headers_signature_matches_documented_scheme(scraper, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1700000000.0)
    payload = '{"query":"{ping}"}'
    headers = scraper._headers(payload)

    expected_sig = hashlib.sha256(
        f"test-app-id1700000000{payload}test-secret".encode("utf-8")
    ).hexdigest()
    assert f"Signature={expected_sig}" in headers["Authorization"]
    assert "Credential=test-app-id" in headers["Authorization"]
    assert "Timestamp=1700000000" in headers["Authorization"]


# ── _post: error classification ─────────────────────────────────────────────

def _fake_post(status_code=200, json_body=None, text=""):
    def _post(url, data, headers, timeout):
        return SimpleNamespace(
            status_code=status_code,
            text=text,
            json=lambda: json_body or {},
            raise_for_status=lambda: None,
        )
    return _post


def test_post_raises_auth_error_on_401(scraper, monkeypatch):
    monkeypatch.setattr(shopee_api.requests, "post", _fake_post(status_code=401, text="denied"))
    with pytest.raises(ShopeeAuthError):
        scraper._post("{ping}")


def test_post_raises_auth_error_on_403(scraper, monkeypatch):
    monkeypatch.setattr(shopee_api.requests, "post", _fake_post(status_code=403, text="denied"))
    with pytest.raises(ShopeeAuthError):
        scraper._post("{ping}")


def test_post_raises_auth_error_on_graphql_credential_error(scraper, monkeypatch):
    body = {"errors": [{"message": "Invalid Credential"}]}
    monkeypatch.setattr(shopee_api.requests, "post", _fake_post(json_body=body))
    with pytest.raises(ShopeeAuthError):
        scraper._post("{ping}")


def test_post_raises_plain_runtime_error_on_non_auth_graphql_error(scraper, monkeypatch):
    body = {"errors": [{"message": "rate limited"}]}
    monkeypatch.setattr(shopee_api.requests, "post", _fake_post(json_body=body))
    with pytest.raises(RuntimeError) as exc_info:
        scraper._post("{ping}")
    assert not isinstance(exc_info.value, ShopeeAuthError)


def test_post_returns_data_on_success(scraper, monkeypatch):
    body = {"data": {"productOfferV2": {"nodes": []}}}
    monkeypatch.setattr(shopee_api.requests, "post", _fake_post(json_body=body))
    assert scraper._post("{ping}") == body["data"]


# ── fetch_listings_sync: credentials + MAX_LLM_CALLS_PER_RUN ───────────────

def test_fetch_listings_sync_raises_without_credentials(monkeypatch):
    monkeypatch.delenv("SHOPEE_APP_ID", raising=False)
    monkeypatch.delenv("SHOPEE_APP_SECRET", raising=False)
    scraper = ShopeeAffiliateScraper()
    with pytest.raises(ShopeeAuthError):
        scraper._fetch_listings_sync()


def test_fetch_listings_sync_stops_at_max_calls(scraper, monkeypatch):
    # Regression guard for docs/decisions.md #18: a scraper that ignores its
    # own cap re-parses the whole result set every night, and the cost grows
    # with total inventory instead of new listings.
    monkeypatch.setenv("MAX_LLM_CALLS_PER_RUN", "2")
    scraper = ShopeeAffiliateScraper()

    nodes = [
        {"itemId": i, "shopId": 1, "productName": f"MacBook Pro {i}",
         "priceMin": "30000", "productLink": f"https://shopee.tw/p/{i}"}
        for i in range(5)
    ]
    monkeypatch.setattr(scraper, "_fetch_page", lambda keyword, page: (nodes, False))

    listings = scraper._fetch_listings_sync()
    assert len(listings) == 2


# ── _to_listing: L1 gatekeeper ──────────────────────────────────────────────

def _node(**overrides):
    base = {
        "itemId": 1, "shopId": 2, "productName": "MacBook Pro M3 16G/512G",
        "priceMin": "35000", "productLink": "https://shopee.tw/product/2/1",
    }
    base.update(overrides)
    return base


def test_to_listing_accepts_valid_node(scraper):
    listing = scraper._to_listing(_node())
    assert listing is not None
    assert listing.source == "shopee"
    assert listing.status == "available"
    assert "35000" in listing.body_content


@pytest.mark.parametrize("title", ["MacBook 保護貼", "MacBook 零件機出售", "MacBook 專用轉接充電線"])
def test_to_listing_rejects_excluded_titles(scraper, title):
    assert scraper._to_listing(_node(productName=title)) is None


@pytest.mark.parametrize("price", ["1000", "999999"])
def test_to_listing_rejects_out_of_range_price(scraper, price):
    assert scraper._to_listing(_node(priceMin=price)) is None


def test_to_listing_rejects_empty_title(scraper):
    assert scraper._to_listing(_node(productName="  ")) is None


def test_to_listing_falls_back_to_synthetic_url_without_product_link(scraper):
    listing = scraper._to_listing(_node(productLink=None))
    assert listing.url == "https://shopee.tw/product/2/1"


def test_to_listing_body_capped_at_l3_limit(scraper):
    listing = scraper._to_listing(_node(shopName="賣" * 2000))
    assert len(listing.body_content) <= shopee_api.L3_BODY_MAX_CHARS


def test_to_listing_notes_price_range_for_variants(scraper):
    listing = scraper._to_listing(_node(priceMin="30000", priceMax="45000"))
    assert "30000" in listing.body_content and "45000" in listing.body_content
