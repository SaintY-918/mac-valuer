"""Shopee Affiliate Open API scraper.

Replaces the browser-based ShopeeScraper when SHOPEE_APP_ID / SHOPEE_APP_SECRET
are configured. Talks to the official affiliate GraphQL endpoint over plain
HTTP, so there is no browser, no cookie jar, no CAPTCHA, and no datacenter-IP
block -- it runs unchanged inside GitHub Actions.

Spec compliance (.spec/specs/scraper/spec.md 3.X):
  - inherits BaseScraper, source="shopee"
  - L1 gatekeeper: 5000 <= price <= 150000, title exclusion list
  - L2 gatekeeper: sold-out items are not emitted
  - L3 gatekeeper: body_content capped at 800 chars
  - MAX_LLM_CALLS_PER_RUN graceful shutdown
"""

import asyncio
import hashlib
import json
import logging
import os
import time

import requests

from src.scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://open-api.affiliate.shopee.tw/graphql"
DEFAULT_KEYWORDS = "二手 MacBook"
HTTP_TIMEOUT_SECONDS = 20

# Shared with the browser scraper -- keep both in sync.
EXCLUDE_TITLES = ["殼", "膜", "零件機", "報廢", "充電線", "保護貼", "貼膜", "支架", "轉接"]

L1_MIN_PRICE = 5000
L1_MAX_PRICE = 150000
L3_BODY_MAX_CHARS = 800

# productOfferV2 sortType: 1=Relevance 2=Sales 3=PriceDesc 4=PriceAsc 5=CommissionDesc
_SORT_RELEVANCE = 1


class ShopeeAuthError(RuntimeError):
    """Credentials rejected by the affiliate API -- surfaced so the pipeline can
    report a hard failure instead of silently reporting zero listings."""


def credentials_configured() -> bool:
    return bool(os.getenv("SHOPEE_APP_ID", "").strip() and os.getenv("SHOPEE_APP_SECRET", "").strip())


def _normalise_price(raw) -> float:
    """Affiliate API returns TWD as a string or number. The internal PDP API used
    micro-units (value * 100000); tolerate both so a format change cannot make
    every listing silently fail the L1 price gate."""
    if raw is None:
        return 0.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if val >= L1_MAX_PRICE * 1000:  # unmistakably micro-units
        val /= 100000
    return val


class ShopeeAffiliateScraper(BaseScraper):
    def __init__(self):
        self._app_id = os.getenv("SHOPEE_APP_ID", "").strip()
        self._app_secret = os.getenv("SHOPEE_APP_SECRET", "").strip()
        self._endpoint = os.getenv("SHOPEE_AFFILIATE_ENDPOINT", DEFAULT_ENDPOINT).strip()
        self._keywords = [
            k.strip() for k in os.getenv("SHOPEE_KEYWORDS", DEFAULT_KEYWORDS).split(",") if k.strip()
        ]
        self._pages = int(os.getenv("SHOPEE_API_PAGES", "3"))
        self._limit = int(os.getenv("SHOPEE_API_LIMIT", "50"))
        self._max_calls = int(os.getenv("MAX_LLM_CALLS_PER_RUN", "100"))
        self._delay = float(os.getenv("SHOPEE_API_DELAY", "1"))

    # ------------------------------------------------------------------
    # Signed transport
    # ------------------------------------------------------------------

    def _headers(self, payload: str) -> dict:
        ts = int(time.time())
        base = f"{self._app_id}{ts}{payload}{self._app_secret}"
        signature = hashlib.sha256(base.encode("utf-8")).hexdigest()
        return {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self._app_id}, Timestamp={ts}, Signature={signature}",
        }

    def _post(self, query: str) -> dict:
        payload = json.dumps({"query": query}, ensure_ascii=False, separators=(",", ":"))
        resp = requests.post(
            self._endpoint,
            data=payload.encode("utf-8"),
            headers=self._headers(payload),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code in (401, 403):
            raise ShopeeAuthError(
                f"Affiliate API rejected credentials: HTTP {resp.status_code} {resp.text[:200]}"
            )
        resp.raise_for_status()
        body = resp.json()
        errors = body.get("errors")
        if errors:
            msg = json.dumps(errors, ensure_ascii=False)[:300]
            # Shopee reports auth problems inside the GraphQL errors array too.
            if any(tok in msg.lower() for tok in ("credential", "signature", "unauthor", "invalid appid")):
                raise ShopeeAuthError(f"Affiliate API auth error: {msg}")
            raise RuntimeError(f"Affiliate API GraphQL error: {msg}")
        return body.get("data") or {}

    # ------------------------------------------------------------------
    # Query construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query(keyword: str, page: int, limit: int) -> str:
        # Args are inlined rather than passed as GraphQL variables -- the affiliate
        # endpoint signs the raw body and is unreliable with a variables map.
        kw = json.dumps(keyword, ensure_ascii=False)
        return (
            "{productOfferV2("
            f"keyword:{kw},sortType:{_SORT_RELEVANCE},page:{page},limit:{limit}"
            "){nodes{itemId shopId productName price priceMin priceMax sales "
            "imageUrl shopName productLink offerLink ratingStar} "
            "pageInfo{page limit hasNextPage}}}"
        )

    def _fetch_page(self, keyword: str, page: int) -> tuple[list[dict], bool]:
        data = self._post(self._build_query(keyword, page, self._limit))
        offer = data.get("productOfferV2") or {}
        nodes = offer.get("nodes") or []
        has_next = bool((offer.get("pageInfo") or {}).get("hasNextPage"))
        logger.info("Affiliate API: keyword=%r page=%d -> %d nodes", keyword, page, len(nodes))
        return nodes, has_next

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    async def fetch_detail(self, _url: str) -> str:
        # The affiliate API exposes no product description field.
        return ""

    async def fetch_listings(self) -> list[RawListing]:
        return await asyncio.to_thread(self._fetch_listings_sync)

    def _fetch_listings_sync(self) -> list[RawListing]:
        if not credentials_configured():
            raise ShopeeAuthError("SHOPEE_APP_ID / SHOPEE_APP_SECRET are not set")

        raw_nodes: list[dict] = []
        for keyword in self._keywords:
            for page in range(1, self._pages + 1):
                nodes, has_next = self._fetch_page(keyword, page)
                raw_nodes.extend(nodes)
                if not has_next or not nodes:
                    break
                time.sleep(self._delay)

        # Dedup across keywords and pages by (shopId, itemId).
        seen: set[tuple] = set()
        unique: list[dict] = []
        for node in raw_nodes:
            key = (node.get("shopId"), node.get("itemId"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(node)
        logger.info("Affiliate API: %d nodes, %d unique after dedup", len(raw_nodes), len(unique))

        listings: list[RawListing] = []
        for node in unique:
            if len(listings) >= self._max_calls:
                logger.warning("MAX_LLM_CALLS_PER_RUN (%d) reached", self._max_calls)
                break
            listing = self._to_listing(node)
            if listing:
                listings.append(listing)

        logger.info("L1 filter: %d / %d nodes passed", len(listings), len(unique))
        return listings

    def _to_listing(self, node: dict) -> RawListing | None:
        title = (node.get("productName") or "").strip()
        if not title:
            return None
        # L1: title exclusion
        if any(w in title for w in EXCLUDE_TITLES):
            return None

        # priceMin is the real entry price when an item has variants; the
        # affiliate API does not expose per-model rows, so the cheapest variant
        # is the best available proxy.
        price = _normalise_price(node.get("priceMin") or node.get("price"))
        # L1: price range
        if not (L1_MIN_PRICE <= price <= L1_MAX_PRICE):
            return None

        item_id, shop_id = node.get("itemId"), node.get("shopId")
        url = node.get("productLink") or (
            f"https://shopee.tw/product/{shop_id}/{item_id}" if shop_id and item_id else None
        )
        if not url:
            return None

        # Keep the annotation format that main.py's price regex depends on.
        body = f"【系統自動標註：此商品售價為 {int(price)} 元】\n{title}"
        price_max = _normalise_price(node.get("priceMax"))
        if price_max > price:
            body += f"\n（此商品有多種規格，價格區間 {int(price)} ~ {int(price_max)} 元）"
        shop_name = node.get("shopName")
        if shop_name:
            body += f"\n賣場：{shop_name}"

        return RawListing(
            url=url,
            title=title,
            body_content=body[:L3_BODY_MAX_CHARS],  # L3
            source="shopee",
            status="available",  # L2: productOfferV2 only returns live offers
        )
