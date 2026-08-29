"""PTT MacShop scraper.

Plain HTTP, no browser. The listing index is PTT's Atom feed, and every article
is a static server-rendered page whose body sits in <div id="main-content">.

This file used to launch Chromium for each detail page, and that cost the
project three nights of CI. ba2d143 deleted `playwright install` from the daily
workflow because the browser-based Shopee scraper was leaving CI — a true reason
about the source being removed, never checked against PTT, the source being
kept. Every nightly run from 2026-08-26 died at browser launch.

The browser was buying nothing. `_main_content_text()` was compared against
Playwright's `inner_text()` on six live articles: identical once whitespace is
normalised. A dependency that cannot be forgotten is better than one that is
remembered most of the time.
"""

import asyncio
import html
import logging
import os
import re
from typing import Optional

import feedparser
import requests

from src.scrapers.base import BaseScraper, RawListing
from src.utils.chip_extract import mentions_apple_silicon

logger = logging.getLogger(__name__)

_RSS_URL = "https://www.ptt.cc/atom/MacShop.xml"
_EXCLUDE_TITLES = ["徵", "[交換]", "intel", "i5", "i7", "i9", "2017", "2018"]
_SOLD_KEYWORDS = ["售出", "已售出", "Sold", "sold", "已出"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 15

# Turning markup into the text a reader sees. Block-level closing tags become
# newlines and inline ones vanish, which is the rule the browser was applying.
_SCRIPTS = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_BLOCK_END = re.compile(r"</(div|p|li|tr|h\d)>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t　]+")
_BLANK_RUN = re.compile(r"\n{3,}")


def _main_content_text(page_html: str) -> str:
    """The article body as text, matching what Playwright's inner_text() returned.

    Everything after the opening tag is taken rather than trying to balance the
    nested divs inside main-content: the caller cuts at the signature separator
    long before the page footer, so the closing boundary never matters.
    """
    marker = page_html.find('id="main-content"')
    if marker == -1:
        return ""
    body = page_html[page_html.find(">", marker) + 1:]
    body = _SCRIPTS.sub("", body)
    body = _BR.sub("\n", body)
    body = _BLOCK_END.sub("\n", body)
    body = _TAG.sub("", body)
    body = html.unescape(body)
    body = _SPACES.sub(" ", body)
    body = "\n".join(line.strip() for line in body.split("\n"))
    return _BLANK_RUN.sub("\n\n", body).strip()


class PTTScraper(BaseScraper):
    def __init__(self):
        self._sem = asyncio.Semaphore(int(os.getenv("SCRAPER_CONCURRENCY", "5")))
        self._delay = float(os.getenv("SCRAPER_DELAY_SECONDS", "1"))

    def _get(self, url: str) -> str:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        # PTT serves UTF-8 but does not always say so in the headers, and letting
        # requests guess turns every Chinese article into mojibake.
        resp.encoding = "utf-8"
        return resp.text

    async def _body_text(self, url: str) -> str:
        page_html = await asyncio.to_thread(self._get, url)
        text = _main_content_text(page_html)
        return text.split("--")[0] if "--" in text else text

    async def fetch_detail(self, url: str) -> str:
        async with self._sem:
            try:
                return await self._body_text(url)
            except Exception as e:
                logger.warning("fetch_detail failed for %s: %s", url, e)
                return ""

    async def _fetch_one(self, url: str, title: str) -> Optional[RawListing]:
        async with self._sem:
            try:
                text = await self._body_text(url)
                if not text:
                    return None
                await asyncio.sleep(self._delay)
                return RawListing(
                    url=url,
                    title=title,
                    body_content=text,
                    source="ptt",
                    status="sold" if any(kw in text for kw in _SOLD_KEYWORDS) else "available",
                )
            except Exception as e:
                logger.warning("Scrape failed for %s: %s", url, e)
                return None

    async def fetch_listings(self) -> list[RawListing]:
        feed = await asyncio.to_thread(feedparser.parse, _RSS_URL)

        # An unreachable feed comes back from feedparser as an object with no
        # entries, not as an exception. Returning [] there would be reported as
        # a quiet night on a board that is never quiet — the same confusion
        # between "nothing matched" and "the scraper is broken" that the
        # heartbeat exists to prevent.
        if not feed.entries:
            raise RuntimeError(
                f"PTT feed returned no entries (bozo={getattr(feed, 'bozo', '?')}, "
                f"{getattr(feed, 'bozo_exception', 'no exception')})"
            )

        candidates: list[tuple[str, str]] = []
        for entry in feed.entries:
            title: str = entry.title
            url: str = entry.link
            title_lower = title.lower()
            if any(tag in title for tag in _EXCLUDE_TITLES):
                continue
            # Was a literal ["m1", "m2", "m3", "m4"], which silently dropped
            # every M5 and every A-series machine — the newest and priciest
            # listings on the board. main.py had already been fixed for exactly
            # this, twice; the list here was a third copy of the same mistake.
            if not mentions_apple_silicon(title):
                continue
            if "macbook" not in title_lower:
                continue
            candidates.append((url, title))

        logger.info(
            "RSS: %d entries, %d pass filter — fetching detail pages...",
            len(feed.entries),
            len(candidates),
        )

        if not candidates:
            return []

        results = await asyncio.gather(*[self._fetch_one(url, title) for url, title in candidates])

        listings = [r for r in results if r is not None]
        sold = sum(1 for lst in listings if lst.status == "sold")
        logger.info(
            "Fetched %d listings — %d available, %d sold",
            len(listings),
            len(listings) - sold,
            sold,
        )
        return listings
