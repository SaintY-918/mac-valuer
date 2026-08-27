"""Answer one question: can the browser scraper reach Shopee from here?

    python -m src.scripts.probe_shopee_browser

Touches no database and calls no LLM — it runs ShopeeScraper's browser path and
reports what came back. Built to settle whether Shopee blocks GitHub's runners,
which was assumed but never tested: the cloud runs returned zero for three other
reasons (no session on the runner, headless hitting the login wall, camoufox's
browser never fetched), so the IP question never got a fair trial.

Exit codes: 0 listings found · 1 blocked/failed · 2 misconfigured.
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from src.scrapers.shopee import ShopeeScraper, ShopeeSessionExpired  # noqa: E402
from src.scrapers.shopee_api import credentials_configured  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main() -> int:
    if credentials_configured():
        print("SHOPEE_APP_ID is set, so ShopeeScraper would use the affiliate API.")
        print("This probe tests the BROWSER path — unset the credentials to use it.")
        return 2

    where = "GitHub Actions runner" if os.getenv("GITHUB_ACTIONS") else "this machine"
    print(f"Probing Shopee's browser path from {where}\n")

    scraper = ShopeeScraper()
    if not scraper._state_path.exists():
        print(f"No session at {scraper._state_path}.")
        print("Locally: run once with SHOPEE_HEADLESS=false to log in.")
        print("In CI: set the SHOPEE_STATE_B64 secret (see export_shopee_session.py).")
        return 2

    try:
        listings = asyncio.run(scraper.fetch_listings())
    except ShopeeSessionExpired as e:
        print("\n" + "=" * 62)
        print("BLOCKED — hit the login / anti-bot wall.")
        print("=" * 62)
        print(f"\n{e}\n")
        print("The session was present, so this is Shopee refusing this client:")
        print("either the IP range is blocked, or moving a Taiwan session to this")
        print("network tripped re-verification. Either way this environment cannot")
        print("run the browser scraper — keep it on a residential connection.")
        return 1
    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        return 1

    print("\n" + "=" * 62)
    if listings:
        print(f"WORKS — {len(listings)} listings returned from {where}.")
    else:
        print(f"Ran without error but returned 0 listings from {where}.")
    print("=" * 62)

    for lst in listings[:10]:
        price = (lst.body_content.split("售價為 ")[1].split(" 元")[0]
                 if "售價為 " in lst.body_content else "?")
        print(f"  {price:>7} TWD  {lst.title[:60]}")

    if not listings:
        print("\nNo exception and no results: the search page loaded but yielded")
        print("nothing — likely served a bot-check page rather than results.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
