import asyncio
import logging
import os
from typing import Optional

import feedparser
from playwright.async_api import Browser, async_playwright

from src.scrapers.base import BaseScraper, RawListing

logger = logging.getLogger(__name__)

_RSS_URL = "https://www.ptt.cc/atom/MacShop.xml"
_M_CHIPS = ["m1", "m2", "m3", "m4"]
_EXCLUDE_TITLES = ["徵", "[交換]", "intel", "i5", "i7", "i9", "2017", "2018"]
_SOLD_KEYWORDS = ["售出", "已售出", "Sold", "sold", "已出"]


class PTTScraper(BaseScraper):
    def __init__(self):
        self._sem = asyncio.Semaphore(int(os.getenv("SCRAPER_CONCURRENCY", "5")))
        self._delay = float(os.getenv("SCRAPER_DELAY_SECONDS", "1"))

    async def fetch_detail(self, url: str) -> str:
        """One-off detail fetch (opens its own browser — use fetch_listings for batches)."""
        async with self._sem:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    el = await page.query_selector("#main-content")
                    if not el:
                        return ""
                    text = await el.inner_text()
                    return text.split("--")[0] if "--" in text else text
                except Exception as e:
                    logger.warning("fetch_detail failed for %s: %s", url, e)
                    return ""
                finally:
                    await browser.close()

    async def _fetch_one(self, browser: Browser, url: str, title: str) -> Optional[RawListing]:
        """Fetch a single page using a shared browser instance."""
        async with self._sem:
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                el = await page.query_selector("#main-content")
                if not el:
                    return None
                text = await el.inner_text()
                if "--" in text:
                    text = text.split("--")[0]
                await asyncio.sleep(self._delay)
                is_sold = any(kw in text for kw in _SOLD_KEYWORDS)
                return RawListing(
                    url=url,
                    title=title,
                    body_content=text,
                    source="ptt",
                    status="sold" if is_sold else "available",
                )
            except Exception as e:
                logger.warning("Scrape failed for %s: %s", url, e)
                return None
            finally:
                await page.close()

    async def fetch_listings(self) -> list[RawListing]:
        feed = await asyncio.to_thread(feedparser.parse, _RSS_URL)

        candidates: list[tuple[str, str]] = []
        for entry in feed.entries:
            title: str = entry.title
            url: str = entry.link
            title_lower = title.lower()
            if any(tag in title for tag in _EXCLUDE_TITLES):
                continue
            if not any(chip in title_lower for chip in _M_CHIPS):
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

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                tasks = [self._fetch_one(browser, url, title) for url, title in candidates]
                results = await asyncio.gather(*tasks)
            finally:
                await browser.close()

        listings = [r for r in results if r is not None]
        sold = sum(1 for lst in listings if lst.status == "sold")
        logger.info(
            "Fetched %d listings — %d available, %d sold",
            len(listings),
            len(listings) - sold,
            sold,
        )
        return listings
