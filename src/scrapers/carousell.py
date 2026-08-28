"""Carousell (旋轉拍賣) scraper.

Plain HTTP, no browser: category and product pages are server-rendered and every
field we need sits in a schema.org JSON-LD block.

This file used to claim that made it safe on a GitHub runner, unlike the Shopee
browser path. That was wrong. On 2026-08-28 the scheduled run failed with a 403
on the sitemap itself while the same request from a residential IP returned 200
— so Carousell filters on something about the caller, not on how the page is
rendered. Not needing a browser is not the same as not being blocked.

See src/scripts/probe_carousell.py for what is being measured, and
docs/decisions.md for where that landed.

robots.txt compliance (checked 2026-08-27):
  Disallow: /search/   -> never used
  Disallow: /*?        -> no query-string URLs; that rules out ?page= pagination
  Allow:    /api-service/*?
  Sitemap:  https://tw.carousell.com/sitemap.xml

Listings therefore come from the product sitemap Carousell publishes, and only
`/p/...` product pages are fetched. The sitemap carries <lastmod> per URL, so a
run reads the newest listings rather than a random slice.
"""

import asyncio
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone

import requests

from src.scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

DEFAULT_SITEMAP = "https://tw.carousell.com/sitemaps/products/tw-computers-tech.xml"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 25

_MODEL_RE = re.compile(r"macbook|mac\s*book", re.I)
# Accessories whose titles also say "MacBook". Shared intent with the Shopee
# exclusion list; kept separate because Carousell's wording differs.
_EXCLUDE_TITLES = [
    "殼", "膜", "零件機", "報廢", "充電線", "保護貼", "貼膜", "支架", "轉接",
    "包", "袋", "貼紙", "鍵盤膜", "擴充座", "底座", "電源線", "維修", "收購",
    # Windows laptops sold as "MacBook-like" put the word in their slug.
    "類macbook", "類 macbook", "仿macbook",
]
_SOLD_KEYWORDS = ["已售出", "售出", "已賣出", "sold", "已完售"]

L1_MIN_PRICE = 5000
L1_MAX_PRICE = 150000
L3_BODY_MAX_CHARS = 800

_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_URL_RE = re.compile(r"<url>\s*<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>", re.S)


def _parse_lastmod(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw.strip())
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


class CarousellScraper(BaseScraper):
    def __init__(self):
        self._sitemap = os.getenv("CAROUSELL_SITEMAP", DEFAULT_SITEMAP)
        self._max_items = int(os.getenv("CAROUSELL_MAX_ITEMS", "30"))
        self._delay = float(os.getenv("CAROUSELL_DELAY", "1.5"))
        self._sem = asyncio.Semaphore(int(os.getenv("SCRAPER_CONCURRENCY", "3")))

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, url: str) -> str:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text

    # ------------------------------------------------------------------
    # Sitemap
    # ------------------------------------------------------------------

    def _candidate_urls(self) -> list[str]:
        """Newest MacBook product URLs from the sitemap, most recent first."""
        xml = self._get(self._sitemap)
        entries = _URL_RE.findall(xml)
        logger.info("Carousell sitemap: %d product URLs", len(entries))

        macbooks = []
        for loc, lastmod in entries:
            # Slugs are percent-encoded; decode before matching so Chinese
            # titles containing the model name are not missed.
            slug = urllib.parse.unquote(loc)
            if not _MODEL_RE.search(slug):
                continue
            if any(w in slug for w in _EXCLUDE_TITLES):
                continue
            macbooks.append((_parse_lastmod(lastmod), loc))

        macbooks.sort(key=lambda x: x[0], reverse=True)
        logger.info("Carousell: %d MacBook URLs after slug filter, taking newest %d",
                    len(macbooks), self._max_items)
        return [loc for _, loc in macbooks[: self._max_items]]

    # ------------------------------------------------------------------
    # Product page
    # ------------------------------------------------------------------

    @staticmethod
    def _product_jsonld(html: str) -> dict | None:
        for block in _JSONLD_RE.findall(html):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
        return None

    def _fetch_one(self, url: str) -> RawListing | None:
        try:
            product = self._product_jsonld(self._get(url))
        except Exception as e:
            # One bad listing must not sink the batch.
            logger.warning("Carousell fetch failed for %s: %s", url, e)
            return None

        if not product:
            logger.warning("No JSON-LD Product on %s — page layout may have changed", url)
            return None

        title = (product.get("name") or "").strip()
        if not title or any(w in title for w in _EXCLUDE_TITLES):
            return None
        # The slug is not enough. A Honor laptop advertised as "鋁合金類macbook"
        # matches on the URL, and one seller's title was literally "888" with a
        # 99999 placeholder price. Requiring the model name in the title itself
        # rejects both.
        if not _MODEL_RE.search(title):
            logger.debug("Title is not a MacBook, skipping: %s", title[:40])
            return None

        offers = product.get("offers") or {}
        try:
            price = float(offers.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        # L1: price range
        if not (L1_MIN_PRICE <= price <= L1_MAX_PRICE):
            return None

        description = (product.get("description") or "").strip()

        # L2: schema.org availability is authoritative; the description is a
        # backstop for sellers who mark a sale in text without updating status.
        availability = str(offers.get("availability") or "")
        sold = "InStock" not in availability or any(
            k in description.lower() for k in _SOLD_KEYWORDS)

        body = f"【系統自動標註：此商品售價為 {int(price)} 元】\n{description}"
        return RawListing(
            url=url,
            title=title,
            body_content=body[:L3_BODY_MAX_CHARS],  # L3
            source="carousell",
            status="sold" if sold else "available",
        )

    # ------------------------------------------------------------------
    # BaseScraper interface
    # ------------------------------------------------------------------

    async def fetch_detail(self, url: str) -> str:
        def _run() -> str:
            product = self._product_jsonld(self._get(url))
            return (product or {}).get("description", "")

        try:
            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning("fetch_detail failed for %s: %s", url, e)
            return ""

    async def fetch_listings(self) -> list[RawListing]:
        urls = await asyncio.to_thread(self._candidate_urls)
        if not urls:
            # Not an error the pipeline should mask: the sitemap is always
            # populated, so an empty result means the format changed.
            raise RuntimeError(
                f"Carousell sitemap yielded no MacBook URLs ({self._sitemap}) — "
                "the sitemap layout or slug format has probably changed"
            )

        async def one(url: str) -> RawListing | None:
            async with self._sem:
                listing = await asyncio.to_thread(self._fetch_one, url)
                await asyncio.sleep(self._delay)
                return listing

        results = await asyncio.gather(*(one(u) for u in urls))
        listings = [r for r in results if r is not None]
        logger.info("Carousell: %d listings from %d product pages", len(listings), len(urls))
        return listings
