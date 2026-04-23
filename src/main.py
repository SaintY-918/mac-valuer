import asyncio
import logging
import json
import re
import time

import pandas as pd
from tabulate import tabulate

from src.calculator.score_engine import get_vfm_score
from src.database.db_manager import DBManager
from src.models.mac_spec import MacBookSpec
from src.parser.llm_parser import extract_specs_from_text, parse_deal_llm
from src.scrapers.ptt import PTTScraper
from src.scrapers.shopee import ShopeeScraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def force_extract_chip(title: str) -> str | None:
    t = title.upper()
    tiers = ["M4 MAX", "M4 PRO", "M4", "M3 MAX", "M3 PRO", "M3", "M2 MAX", "M2 PRO", "M2", "M1 MAX", "M1 PRO", "M1"]
    for chip in tiers:
        if chip in t:
            return chip
    return None


def run_valuation_pipeline(source: str = "all"):
    db = DBManager()

    print(f"=== Step 1: Fetching from scrapers (source={source}) ===")
    all_scrapers = {"ptt": PTTScraper(), "shopee": ShopeeScraper()}
    scrapers = [all_scrapers[source]] if source != "all" else list(all_scrapers.values())
    raw_listings = []
    for scraper in scrapers:
        try:
            listings = asyncio.run(scraper.fetch_listings())
            raw_listings.extend(listings)
        except Exception as e:
            logger.error("Scraper %s failed: %s", type(scraper).__name__, e)

    for listing in raw_listings:
        existing = db.get_cached_deal(listing.url)
        if not existing:
            db.save_deal(listing.url, listing.title, listing.body_content,
                         status=listing.status, source=listing.source)
        elif listing.status == "sold" and existing.get("status") != "sold":
            db.save_deal(listing.url, listing.title, listing.body_content,
                         status="sold", source=listing.source)

    print("\n=== Step 2: Database Repair & Hard Extraction ===")
    all_items = db.get_all_deals()

    for i, item in enumerate(all_items):
        url, title, body = item["url"], item["title"], item["body_content"]
        p_json = json.loads(item["parsed_json"]) if item["parsed_json"] else None

        needs_fix = (
            not p_json
            or not p_json.get("chip")
            or p_json.get("chip") == "None"
            or p_json.get("location") == "未知"
            or not p_json.get("price")
            or not p_json.get("ram_gb")
            or not p_json.get("ssd_gb")
            or not p_json.get("screen_size")
        )

        if not needs_fix:
            continue

        print(f"   [{i+1}/{len(all_items)}] REPAIRING: {title[:30]}...")
        clean_title = re.sub(r"\[.*?\]", "[販售]", title)

        spec_obj = parse_deal_llm(clean_title, body)
        res_dict = spec_obj.model_dump() if spec_obj else (p_json or {})

        if not res_dict.get("chip") or res_dict.get("chip") == "None":
            res_dict["chip"] = force_extract_chip(title)  # may still be None — that is correct

        ram, ssd = extract_specs_from_text(title)
        if ram and not res_dict.get("ram_gb"):
            res_dict["ram_gb"] = ram
        if ssd and not res_dict.get("ssd_gb"):
            res_dict["ssd_gb"] = ssd

        if not res_dict.get("price"):
            price_match = re.search(r"(\d{5,6})", title.replace(",", ""))
            if price_match:
                res_dict["price"] = float(price_match.group(1))

        if not res_dict.get("location") or res_dict.get("location") == "未知":
            res_dict["location"] = "未知"

        db.save_deal(url, title, body, res_dict)
        time.sleep(1)

    print("\n=== Step 3: Aggregating Final Data ===")
    all_parsed = db.get_all_parsed_deals()
    if not all_parsed:
        return

    df = pd.DataFrame(all_parsed)
    for col in ["ram_gb", "ssd_gb", "screen_size", "release_year", "price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    print(f"=== Step 4: Scoring {len(df)} items ===")
    final_results = []
    for _, row in df.iterrows():
        try:
            chip = str(row["chip"]) if row["chip"] else None
            price = float(row["price"])
            if price < 5000 or not chip or chip == "None":
                continue

            spec_obj = MacBookSpec(
                chip=chip,
                ram_gb=int(row["ram_gb"] or 8),
                ssd_gb=int(row["ssd_gb"] or 256),
                screen_size=float(row["screen_size"] or 13.3),
                release_year=int(row["release_year"] or 2020),
                series=row.get("series", "Air"),
                price=price,
                location=str(row["location"]),
            )
            score = get_vfm_score(spec_obj)

            final_results.append({
                "Title": row["original_title"][:40] + "...",
                "Chip": chip,
                "RAM": f"{int(spec_obj.ram_gb)}G",
                "SSD": f"{int(spec_obj.ssd_gb)}G",
                "Size": f'{spec_obj.screen_size}"',
                "Year": int(spec_obj.release_year),
                "Price": f"{int(price):,}",
                "Region": str(row["location"]),
                "VFM Score": round(score, 2),
            })
        except Exception as e:
            logger.warning("Scoring error for '%s': %s", row.get("original_title", "?")[:30], e)
            continue

    final_results.sort(key=lambda x: x["VFM Score"], reverse=True)
    pd.DataFrame(final_results).to_csv("valuation_report.csv", index=False, encoding="utf-8-sig")
    print(tabulate(final_results[:15], headers="keys", tablefmt="fancy_grid"))
    print(f"\nDone. Total items in report: {len(final_results)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["ptt", "shopee", "all"], default="all")
    args = parser.parse_args()
    run_valuation_pipeline(source=args.source)
