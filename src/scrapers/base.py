from abc import ABC, abstractmethod
from dataclasses import dataclass


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
