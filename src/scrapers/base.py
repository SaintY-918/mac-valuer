import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RawListing:
    url: str
    title: str
    body_content: str
    source: str = "ptt"
    status: str = "available"  # 'available' | 'sold'


class BaseScraper(ABC):
    @abstractmethod
    async def fetch_listings(self) -> list[RawListing]:
        """Fetch all candidate listings from the source."""
        ...

    @abstractmethod
    async def fetch_detail(self, url: str) -> str:
        """Fetch the full body text of a single listing URL."""
        ...

    # Whether check_listing() can say anything. Every scraper reads a window of
    # recent listings, so a row is seen once and then never again; without a
    # revisit, a deleted listing stays on the dashboard until the 14-day sweep.
    # 22 of 86 live Carousell rows were 404/410 when this was added.
    REVISITS = False

    def check_listing(self, url: str) -> Optional[str]:
        """Look at one stored listing on the platform again.

        Returns 'available', 'sold', 'gone' (the platform says it no longer
        exists), or None when this attempt proves nothing — a timeout, a block,
        a page layout the parser does not recognise. None must never be read as
        'gone': a blocked request says nothing about the listing.
        """
        return None

    async def revisit(self, urls: list[str]) -> dict[str, Optional[str]]:
        """check_listing() for each url, politely. Never raises.

        The semaphore is made here rather than reused from __init__: this runs
        under its own asyncio.run(), and a semaphore that once had to wait in
        fetch_listings' loop is bound to that loop.
        """
        sem = asyncio.Semaphore(int(os.getenv("SCRAPER_CONCURRENCY", "3")))
        delay = getattr(self, "_delay", 1.0)

        async def one(url: str) -> Optional[str]:
            async with sem:
                try:
                    outcome = await asyncio.to_thread(self.check_listing, url)
                except Exception as e:
                    logger.warning("Revisit failed for %s: %s", url, e)
                    outcome = None
                await asyncio.sleep(delay)
                return outcome

        results = await asyncio.gather(*(one(u) for u in urls))
        return dict(zip(urls, results, strict=True))
