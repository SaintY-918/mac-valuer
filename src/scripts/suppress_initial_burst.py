"""One-shot script: seed `last_alerted_price` from current price for all parsed deals.

Run this once before setting DISCORD_WEBHOOK_URL in production to avoid a flood of
alerts for historical inventory. After this runs, the notifier will only send when
a listing's price actually changes (or for genuinely new listings).

Usage:
    python -m src.scripts.suppress_initial_burst
    python -m src.scripts.suppress_initial_burst --dry-run
"""
import argparse
import json
import logging

from src.database.db_manager import DBManager, Deal

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def suppress_initial_burst(dry_run: bool = False) -> dict:
    db = DBManager()
    seeded = 0
    skipped_no_price = 0
    already_set = 0

    with db.Session() as session:
        deals = session.query(Deal).filter(Deal.parsed_json.isnot(None)).all()
        total = len(deals)
        for d in deals:
            if d.last_alerted_price is not None:
                already_set += 1
                continue
            try:
                parsed = json.loads(d.parsed_json) if d.parsed_json else {}
            except (TypeError, ValueError):
                parsed = {}
            price = parsed.get("price")
            if price is None:
                skipped_no_price += 1
                continue
            try:
                d.last_alerted_price = int(float(price))
                seeded += 1
            except (TypeError, ValueError):
                skipped_no_price += 1

        if dry_run:
            session.rollback()
        else:
            session.commit()

    return {
        "total_parsed": total,
        "seeded": seeded,
        "already_set_skipped": already_set,
        "no_price_skipped": skipped_no_price,
        "dry_run": dry_run,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without committing.")
    args = parser.parse_args()

    result = suppress_initial_burst(dry_run=args.dry_run)
    mode = "DRY-RUN (no changes committed)" if result["dry_run"] else "COMMITTED"
    logger.info("Initial-burst suppression %s", mode)
    logger.info("  parsed deals scanned : %d", result["total_parsed"])
    logger.info("  seeded last_alerted  : %d", result["seeded"])
    logger.info("  already had value    : %d", result["already_set_skipped"])
    logger.info("  skipped (no price)   : %d", result["no_price_skipped"])


if __name__ == "__main__":
    main()
