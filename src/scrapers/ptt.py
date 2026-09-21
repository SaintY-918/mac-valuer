"""PTT MacShop scraper.

Plain HTTP, no browser. Listings come from the board's own index pages, walked
back page by page until the posts are older than PTT_LOOKBACK_HOURS, and every
article is a static server-rendered page whose body sits in <div id="main-content">.

The index used to be PTT's Atom feed, which holds the newest 20 posts and no
more. On 2026-09-21 those 20 covered two and a half hours of a board flooded by
the iPhone 18 launch, none of them a Mac; a scraper that runs once a day saw a
tenth of the day and reported nothing for six nights as a quiet board.

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
import time
from typing import Optional
from urllib.parse import urljoin

import requests

from src.scrapers.base import BaseScraper, RawListing
from src.utils.chip_extract import detect_product, mentions_apple_silicon

logger = logging.getLogger(__name__)

_BOARD_URL = "https://www.ptt.cc/bbs/MacShop/index.html"
# The pipeline runs once a day and GitHub's cron can start an hour or more late,
# so reach back further than a day. Overlap costs a few detail requests;
# a gap costs listings.
LOOKBACK_HOURS = float(os.getenv("PTT_LOOKBACK_HOURS", "36"))
# A ceiling, not a target: 20 posts a page, so 40 pages is 800 posts.
MAX_PAGES = int(os.getenv("PTT_MAX_PAGES", "40"))

# The article id carries the posting time: M.<unix seconds>.A.<hex>.html.
# A deleted post keeps its row but loses the link, so it never matches.
_ENTRY_RE = re.compile(
    r'<div class="title">\s*<a href="(/bbs/MacShop/M\.(\d+)\.A\.[0-9A-Fa-f]+\.html)">(.*?)</a>',
    re.S,
)
_PREV_RE = re.compile(r'<a class="btn wide" href="(/bbs/MacShop/index\d+\.html)">&lsaquo; 上頁</a>')
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


def _parse_index(page_html: str) -> tuple[list[tuple[str, str, int]], Optional[str]]:
    """(url, title, posted_at) for each post on one index page, and the previous page.

    Pinned posts sit below <div class="r-list-sep"> on the newest page. They are
    months old, so letting them in would end the walk on the first page.
    """
    listed = page_html.split('class="r-list-sep"')[0]
    entries = [
        (urljoin(_BOARD_URL, path), html.unescape(title).strip(), int(epoch))
        for path, epoch, title in _ENTRY_RE.findall(listed)
    ]
    prev = _PREV_RE.search(page_html)
    return entries, urljoin(_BOARD_URL, prev.group(1)) if prev else None


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

    REVISITS = True

    def check_listing(self, url: str) -> Optional[str]:
        # A seller who deletes the post leaves a 404. Sellers also delete right
        # after selling, so most sales show up here as 'gone', not 'sold'.
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:
            return "gone"
        resp.raise_for_status()
        resp.encoding = "utf-8"
        text = _main_content_text(resp.text)
        if not text:
            return None
        # main-content opens with the title line, so an edited "已售出" title
        # is caught here too.
        body = text.split("--")[0]
        return "sold" if any(kw in body for kw in _SOLD_KEYWORDS) else "available"

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

    def _recent_posts(self) -> list[tuple[str, str]]:
        """(url, title) of every post from the last LOOKBACK_HOURS, newest page first."""
        cutoff = time.time() - LOOKBACK_HOURS * 3600
        posts: dict[str, str] = {}
        url: Optional[str] = _BOARD_URL
        pages = 0
        seen_any = False
        while url and pages < MAX_PAGES:
            entries, url = _parse_index(self._get(url))
            pages += 1
            seen_any = seen_any or bool(entries)
            for link, title, posted_at in entries:
                if posted_at >= cutoff:
                    posts.setdefault(link, title)
            if entries and min(p for _, _, p in entries) < cutoff:
                break
        else:
            if url:
                logger.warning(
                    "PTT: stopped at PTT_MAX_PAGES=%d before reaching %sh back; older posts missed",
                    MAX_PAGES, LOOKBACK_HOURS,
                )

        # The board is never empty. No posts at all means the markup changed,
        # and returning [] would be reported as a quiet night.
        if not seen_any:
            raise RuntimeError(
                f"PTT index yielded no posts ({_BOARD_URL}) — the page layout has probably changed"
            )
        logger.info("PTT: %d posts in the last %sh across %d index pages",
                    len(posts), LOOKBACK_HOURS, pages)
        return list(posts.items())

    async def fetch_listings(self) -> list[RawListing]:
        posts = await asyncio.to_thread(self._recent_posts)

        candidates: list[tuple[str, str]] = []
        for url, title in posts:
            if any(tag in title for tag in _EXCLUDE_TITLES):
                continue
            # Was a literal ["m1", "m2", "m3", "m4"], which silently dropped
            # every M5 and every A-series machine — the newest and priciest
            # listings on the board. main.py had already been fixed for exactly
            # this, twice; the list here was a third copy of the same mistake.
            if not mentions_apple_silicon(title):
                continue
            # Was `"macbook" not in title_lower`, which kept every desktop out.
            # The board also carries iPads and displays; detect_product knows
            # the difference between "Mac mini" and "iPad mini".
            if detect_product(title) is None:
                continue
            candidates.append((url, title))

        logger.info(
            "PTT: %d posts, %d pass filter — fetching detail pages...",
            len(posts),
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
