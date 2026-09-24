"""Keep the Shopee session cleared, and bother a human only when Shopee insists.

    python -m src.scripts.refresh_shopee_session
    python -m src.scripts.refresh_shopee_session --force   # always open a window

Two phases. First a headless probe of one search page: if Shopee still honours
the current clearance the session is re-saved and nothing appears on screen.
Only when that probe hits the wall does a visible browser open, because then
the one thing Shopee wants is a person dragging a puzzle piece -- roughly every
one to two days, and nothing about headless, fingerprints or request volume
changes that (decisions #42).

The order matters: the probe is what makes this safe to run at every logon.
A script that always opened a window would be shut off within a week, and then
the nightly scrape goes back to failing silently at 02:30.

Touches no database and calls no LLM.

Exit codes: 0 session usable · 1 wall hit and nobody cleared it · 2 misconfigured.
"""

import argparse
import asyncio
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from src.scrapers.shopee import ShopeeScraper
from src.scrapers.shopee_api import credentials_configured
from src.utils.logging_setup import configure_logging

try:
    from camoufox.async_api import AsyncCamoufox
except ImportError:  # pragma: no cover - same guard as the scraper
    AsyncCamoufox = None

configure_logging()

# Long enough to walk back to the desk, short enough that a forgotten window
# does not hold a browser open all afternoon.
WAIT_SECONDS = 300
POLL_SECONDS = 2


async def _attempt(scraper, headless: bool) -> int:
    """Read one search page. Returns the item count; saves state when it worked."""
    state = scraper._load_state()
    async with AsyncCamoufox(headless=headless, os="windows") as browser:
        ctx = await browser.new_context(
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            **({"storage_state": state} if state else {}),
        )
        page = await ctx.new_page()
        items = await scraper._search_items(page, scraper._keywords[0], newest=0)

        if not items and not headless:
            print("\nShopee wants a human: the slide captcha, and a login first if it asks.")
            print("Do whatever the window that just opened asks for.")
            print("Leave the window alone afterwards; this closes it for you.\n")
            deadline = time.time() + WAIT_SECONDS
            # A login page is a wall too. This loop used to wait only while the
            # URL said "verify"; on 2026-09-22 Shopee sent the window to its
            # login page instead, the loop took that for "cleared" after one
            # poll, and closed the window two seconds later with the user
            # still looking at the login form.
            while time.time() < deadline:
                await asyncio.sleep(POLL_SECONDS)
                if "verify" not in page.url and "login" not in page.url:
                    break
            await asyncio.sleep(2)
            items = await scraper._search_items(page, scraper._keywords[0], newest=0)

        if items:
            await ctx.storage_state(path=str(scraper._state_path))
        await ctx.close()
        return len(items)


async def _refresh(force: bool) -> int:
    scraper = ShopeeScraper()

    if not force:
        n = await _attempt(scraper, headless=True)
        if n:
            print(f"Clearance still good: {n} items readable, session re-saved. No window needed.")
            return 0
        print("Headless probe hit the wall. Opening a window for you.")

    n = await _attempt(scraper, headless=False)
    if n:
        print(f"Cleared. {n} items readable; session saved to {scraper._state_path}.")
        print("The scheduled run can work headless from here, for a day or two.")
        return 0

    print("Nobody cleared the wall in time. Nothing was saved.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="skip the headless probe and open a window straight away")
    args = parser.parse_args()

    if credentials_configured():
        print("SHOPEE_APP_ID is set, so the affiliate API is in use.")
        print("The browser path, and this captcha ritual, does not apply.")
        return 2
    if AsyncCamoufox is None:
        print("camoufox is not installed. Run: pip install camoufox && python -m camoufox fetch")
        return 2
    return asyncio.run(_refresh(args.force))


if __name__ == "__main__":
    sys.exit(main())
