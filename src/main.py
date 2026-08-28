import asyncio
import json
import logging
import os
import re
from collections import defaultdict

import pandas as pd
from tabulate import tabulate

from src.calculator.score_engine import get_vfm_score
from src.database.db_manager import DBManager
from src.models.mac_spec import MacBookSpec
from src.notifier import send_alert, send_heartbeat
from src.parser.condition_flags import defects_for, find_defects
from src.parser.llm_parser import (
    extract_specs_from_text,
    infer_correct_year,
    parse_deal_llm,
)
from src.scrapers.carousell import CarousellScraper
from src.scrapers.ptt import PTTScraper
from src.scrapers.shopee import ShopeeScraper

DEFAULT_ALERT_VFM_THRESHOLD = 500.0

# A listing is retired only after this many days without being seen again. Both
# scrapers read a rolling window, not full inventory, so "missing from this run"
# is not evidence a listing is gone — see DBManager.sweep_stale.
STALE_DAYS = int(os.getenv("STALE_DAYS", "14"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Matches any Apple Silicon generation rather than a hardcoded list. The list
# stopped at M4, so every M5 listing came back with no chip and was discarded by
# the filter below — silently losing the newest and priciest machines, which are
# exactly the ones worth tracking. A regex means the next generation needs only
# a benchmark entry, not a code change here.
# Two families: the M-series, and the A-series that arrived with the MacBook
# Neo. "A18 Pro" is a real Mac chip, and a title carrying one used to yield no
# chip at all, which meant the listing was discarded outright.
_CHIP_RE = re.compile(r"\b([MA])(\d{1,2})\s*(PRO|MAX|ULTRA)?\b", re.I)


def force_extract_chip(title: str) -> str | None:
    """Best chip found in the title, preferring the highest tier mentioned.

    Sellers pad titles with keywords, so "M1 Pro Max" turns up even though no
    such chip exists. Only the variant adjacent to the generation counts, which
    reads that as M1 Pro — the lower tier. That is the safe direction: a lower
    benchmark understates VFM, and an overstated one would fire a false
    bargain alert.
    """
    best = None
    for family, gen, variant in _CHIP_RE.findall(title.upper()):
        name = f"{family.upper()}{int(gen)}" + (f" {variant.title()}" if variant else "")
        # "M4 Max" beats a bare "M4" in the same title; higher generations win.
        # A-series sorts below every M-series rather than by number, or A18
        # would outrank an M5 on the digits alone.
        # Apple ships exactly one A-series Mac chip, so a bare "A18" in a
        # MacBook title is the A18 Pro. Left alone it misses the benchmark
        # table and takes the 5,000 fallback, halving the score of a machine
        # whose seller simply did not type "Pro".
        if family.upper() == "A" and int(gen) == 18 and not variant:
            name = "A18 Pro"
        rank = (0 if family.upper() == "A" else 1,
                int(gen), {"": 0, "PRO": 1, "MAX": 2, "ULTRA": 3}[variant.upper()])
        if best is None or rank > best[0]:
            best = (rank, name)
    return best[1] if best else None


_INVALID_CHIPS = {"unknown", "none", "null", "n/a", ""}


def _read_alert_threshold() -> float:
    raw = os.getenv("ALERT_VFM_THRESHOLD")
    if raw is None or raw.strip() == "":
        return DEFAULT_ALERT_VFM_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        logger.warning("ALERT_VFM_THRESHOLD=%r is not a number; using default %s",
                       raw, DEFAULT_ALERT_VFM_THRESHOLD)
        return DEFAULT_ALERT_VFM_THRESHOLD


def run_valuation_pipeline(source: str = "all", dry_run: bool = False, skip_scrape: bool = False):
    db = DBManager()

    raw_listings = []
    urls_seen_by_source: dict[str, set[str]] = defaultdict(set)
    sources_attempted: set[str] = set()
    upsert_counts: dict[str, int] = defaultdict(int)
    # source -> error string. A source in here failed outright; its 0 count means
    # "broken", not "nothing new today".
    source_errors: dict[str, str] = {}

    if skip_scrape:
        print("=== Step 1: Skipped (--skip-scrape) — reusing existing DB data ===")
    else:
        print(f"=== Step 1: Fetching from scrapers (source={source}) ===")
        all_scrapers = {
            "ptt": PTTScraper(),
            "shopee": ShopeeScraper(),
            "carousell": CarousellScraper(),
        }
        # Name each source by its key. The previous isinstance check assumed
        # exactly two scrapers and would have labelled any third one "ptt",
        # mixing its listings into PTT's counts and sweep.
        if source == "all":
            selected = all_scrapers
        else:
            # Comma-separated so CI can ask for the sources a runner can serve
            # ("ptt,carousell") without running the whole pipeline once per source.
            wanted = [s.strip() for s in source.split(",") if s.strip()]
            unknown = [s for s in wanted if s not in all_scrapers]
            if unknown:
                raise SystemExit(
                    f"unknown source(s): {', '.join(unknown)}. "
                    f"Choose from: {', '.join(all_scrapers)}, all"
                )
            selected = {s: all_scrapers[s] for s in wanted}
        for src_name, scraper in selected.items():
            sources_attempted.add(src_name)
            try:
                listings = asyncio.run(scraper.fetch_listings())
                raw_listings.extend(listings)
                for lst in listings:
                    if lst and lst.url:
                        urls_seen_by_source[src_name].add(lst.url)
            except Exception as e:
                logger.error("Scraper %s failed: %s", type(scraper).__name__, e)
                source_errors[src_name] = f"{type(e).__name__}: {e}"

        for listing in raw_listings:
            src = listing.source or "ptt"
            existing = db.get_cached_deal(listing.url)
            if not existing:
                db.save_deal(listing.url, listing.title, listing.body_content,
                             status=listing.status, source=listing.source)
                upsert_counts[src] += 1
            elif listing.status == "sold" and existing.get("status") != "sold":
                db.save_deal(listing.url, listing.title, listing.body_content,
                             status="sold", source=listing.source)
                upsert_counts[src] += 1

    if dry_run:
        print(f"\n=== DRY-RUN: {len(raw_listings)} listings survived scraper filters ===")
        for lst in raw_listings:
            m = re.search(r'售價為 (\d+) 元', lst.body_content or "")
            price_str = m.group(1) if m else "N/A"
            print(f"  [{lst.source}] {lst.title[:60]}  @  {price_str} TWD")
        return

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
            # A missing year is not cosmetic: the scorer falls back to 2020,
            # which dates a current machine six years old and halves its VFM.
            or not p_json.get("release_year")
        )

        if not needs_fix:
            continue

        print(f"   [{i+1}/{len(all_items)}] REPAIRING: {title[:30]}...")
        clean_title = re.sub(r"\[.*?\]", "[販售]", title)

        spec_obj = parse_deal_llm(clean_title, body)
        res_dict = spec_obj.model_dump() if spec_obj else (p_json or {})

        if not res_dict.get("chip") or res_dict.get("chip") == "None":
            res_dict["chip"] = force_extract_chip(title)  # may still be None — that is correct

        # infer_correct_year() runs inside parse_deal_llm and keys off the chip,
        # so a listing whose chip the LLM missed comes back with no year — and
        # force_extract_chip fills the chip only afterwards. Re-derive the year
        # now that it is known. Without this the scorer fell back to 2020, which
        # dated a brand-new M5 six years old and halved its VFM: two identical
        # listings scored 432 and 204 purely on whether the year survived.
        if not res_dict.get("release_year") and res_dict.get("chip"):
            inferred, _ = infer_correct_year(res_dict, title)
            if inferred:
                res_dict["release_year"] = inferred

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

        chip = res_dict.get("chip")
        if chip is None or str(chip).strip().lower() in _INVALID_CHIPS:
            logger.info("Chip filter: discarding '%s' (chip=%s)", title[:40], chip)
            continue

        # Detected here because this is the only place the body is still in
        # hand. Every read path drops body_content, so a fault described only
        # in the post body was invisible to both the alert and the dashboard.
        res_dict["defects"] = find_defects(title, res_dict.get("condition"), body)

        db.save_deal(url, title, body, res_dict)
        # No sleep here: llm_parser throttles itself to GEMINI_RPM. A flat 1 s
        # wait allowed up to 60 calls a minute against a 15 RPM free-tier cap.

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
            chip_val = row.get("chip")
            if pd.isna(chip_val) or not str(chip_val).strip() or str(chip_val).strip() == "None":
                continue
            chip = str(chip_val)

            price = float(row["price"])
            if price < 5000:
                continue

            series_val = row.get("series")
            if pd.isna(series_val) or not str(series_val).strip():
                series_val = "Air"

            spec_obj = MacBookSpec(
                chip=chip,
                ram_gb=int(row["ram_gb"] or 8),
                ssd_gb=int(row["ssd_gb"] or 256),
                screen_size=float(row["screen_size"] or 13.3),
                release_year=int(row["release_year"] or 2020),
                series=str(series_val),
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
                "_url": row["url"],
                "_source": row.get("source") or "?",
                "_raw_title": row["original_title"],
                "_raw_price": int(price),
                "_raw_score": float(score),
                # The alert calls a listing a bargain; if it is cheap because it
                # is broken, that has to travel with it.
                "_defects": defects_for(row),
            })
        except Exception as e:
            logger.warning("Scoring error for '%s': %s", row.get("original_title", "?")[:30], e)
            continue

    final_results.sort(key=lambda x: x["VFM Score"], reverse=True)

    # Step 5: Notifier — alert on high-VFM listings whose price changed since last alert.
    alerts_sent = 0
    try:
        alerts_sent = _run_notifier(db, final_results)
    except Exception as e:
        logger.error("Notifier step failed (non-fatal): %s", e)

    # Step 6: Sweep — mark URLs that disappeared from this run as 'unavailable'.
    for src_name in sources_attempted:
        if src_name in source_errors:
            # The source errored out, so nothing was refreshed and every row looks
            # stale. Sweeping now would mark the whole source unavailable.
            logger.warning("Sweep skipped for source='%s': scraper failed this run", src_name)
            continue
        n = db.sweep_stale(src_name, max_age_days=STALE_DAYS)
        logger.info("Sweep result: source='%s' marked_unavailable=%d (unseen for >%d days, saw %d urls)",
                    src_name, n, STALE_DAYS, len(urls_seen_by_source.get(src_name, set())))

    # Step 7: Heartbeat — send daily run summary to Discord.
    try:
        send_heartbeat({
            "counts": {src: upsert_counts.get(src, 0) for src in sorted(sources_attempted)},
            "errors": source_errors,
            "alerts_sent": alerts_sent,
        })
    except Exception as e:
        logger.error("Heartbeat step failed (non-fatal): %s", e)

    # Strip private fields before writing the public CSV report.
    public_results = [{k: v for k, v in r.items() if not k.startswith("_")} for r in final_results]
    pd.DataFrame(public_results).to_csv("valuation_report.csv", index=False, encoding="utf-8-sig")
    print(tabulate(public_results[:15], headers="keys", tablefmt="fancy_grid"))
    print(f"\nDone. Total items in report: {len(public_results)}")


def _run_notifier(db: DBManager, final_results: list[dict]) -> int:
    """Return the number of Discord alerts successfully sent this run."""
    threshold = _read_alert_threshold()
    sent = 0
    for row in final_results:
        score = row.get("_raw_score")
        if score is None or score <= threshold:
            continue
        url = row.get("_url")
        price = row.get("_raw_price")
        if not url or price is None:
            continue
        state = db.get_alert_state(url)
        last_alerted = state.get("last_alerted_price") if state else None
        if last_alerted is not None and int(last_alerted) == int(price):
            continue
        ok = send_alert({
            "source": row.get("_source"),
            "title": row.get("_raw_title"),
            "defects": row.get("_defects"),
            "price": price,
            "vfm_score": score,
            "url": url,
        })
        if ok:
            db.update_last_alerted_price(url, price)
            sent += 1
    if sent:
        logger.info("Notifier: sent %d Discord alert(s) above threshold %.0f", sent, threshold)
    return sent


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="all",
                        help="all, one source, or a comma-separated list "
                             "(ptt, shopee, carousell). Example: --source ptt,carousell")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-scrape", action="store_true",
                         help="Skip Step 1 (scraping) and reuse existing DB data — for testing repair/scoring/notifier without hitting network scrapers or LLM quota on new listings.")
    args = parser.parse_args()
    run_valuation_pipeline(source=args.source, dry_run=args.dry_run, skip_scrape=args.skip_scrape)
