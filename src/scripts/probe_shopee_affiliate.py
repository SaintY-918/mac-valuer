"""Probe the Shopee Affiliate Open API to measure second-hand MacBook coverage.

The open question with the affiliate API is whether individual second-hand
sellers are inside the affiliate programme at all. Run this once you have
SHOPEE_APP_ID / SHOPEE_APP_SECRET to find out before committing to it:

    python -m src.scripts.probe_shopee_affiliate
    python -m src.scripts.probe_shopee_affiliate --keyword "MacBook Pro 二手" --pages 2

It prints the raw node count, how many survive the L1 gatekeeper, and a sample
so you can eyeball whether these are real used-machine listings.
"""

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from src.scrapers.shopee_api import (
    ShopeeAffiliateScraper,
    ShopeeAuthError,
    credentials_configured,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Words that suggest a genuinely used machine rather than a new/official listing.
_USED_HINTS = ["二手", "中古", "福利", "整新", "自用", "無傷", "9成", "九成", "近全新"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default=os.getenv("SHOPEE_KEYWORDS", "二手 MacBook"))
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dump", metavar="PATH", help="write raw nodes to a JSON file")
    args = ap.parse_args()

    if not credentials_configured():
        print("SHOPEE_APP_ID / SHOPEE_APP_SECRET are not set.")
        print("Apply at https://affiliate.shopee.tw/ then put them in .env")
        return 2

    scraper = ShopeeAffiliateScraper()
    scraper._limit = args.limit

    nodes: list[dict] = []
    try:
        for page in range(1, args.pages + 1):
            page_nodes, has_next = scraper._fetch_page(args.keyword, page)
            nodes.extend(page_nodes)
            if not has_next or not page_nodes:
                break
    except ShopeeAuthError as e:
        print(f"\nAUTH FAILED: {e}")
        print("Check the AppID/Secret pair and that the account is approved.")
        return 1
    except Exception as e:
        print(f"\nREQUEST FAILED: {type(e).__name__}: {e}")
        print("If this is a GraphQL field error, the schema differs from what")
        print("_build_query() requests — trim the field list in shopee_api.py.")
        return 1

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump(nodes, f, ensure_ascii=False, indent=2)
        print(f"Raw nodes written to {args.dump}")

    listings = [lst for lst in (scraper._to_listing(n) for n in nodes) if lst]
    used_like = [lst for lst in listings if any(h in lst.title for h in _USED_HINTS)]
    shops = {n.get("shopName") for n in nodes if n.get("shopName")}

    print("\n" + "=" * 62)
    print(f"keyword          : {args.keyword!r}")
    print(f"raw nodes        : {len(nodes)}")
    print(f"passed L1 filter : {len(listings)}")
    print(f"look second-hand : {len(used_like)}")
    print(f"distinct shops   : {len(shops)}")
    print("=" * 62)

    if not nodes:
        print("\nZero nodes. Either the keyword matches nothing in the affiliate")
        print("catalogue, or productOfferV2 needs a different listType for TW.")
    elif not used_like:
        print("\nNodes came back but none look second-hand — the affiliate")
        print("catalogue probably only covers official/new-goods shops.")
        print("=> Coverage is inadequate; keep the browser scraper (plan B).")
    else:
        print(f"\nCoverage looks usable ({len(used_like)} second-hand-ish listings).")
        print("=> Worth switching the pipeline to the affiliate API.")

    print("\n--- sample (up to 15) ---")
    for lst in listings[:15]:
        price = lst.body_content.split("售價為 ")[1].split(" 元")[0] if "售價為 " in lst.body_content else "?"
        mark = "*" if any(h in lst.title for h in _USED_HINTS) else " "
        print(f" {mark} {price:>7} TWD  {lst.title[:64]}")
    print("\n(* = title contains a second-hand keyword)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
