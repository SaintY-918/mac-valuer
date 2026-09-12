"""Re-check every stored series against the title's product name.

    python -m src.scripts.revalidate_series            # dry run, reports only
    python -m src.scripts.revalidate_series --apply    # clear the mismatches

Before desktops were supported, the parser's fallback labelled anything that
was not an Air as "Pro 13". The Shopee search for "二手 MacBook" also returns
the odd Mac mini, so a few desktops are already stored — as 13" laptops, scored
with a laptop form factor and shown under 筆電. Rules changed; stored rows do
not follow on their own (decisions #23).

A mismatch has its parsed_json cleared, not corrected in place: the parse also
sets the year and drops the screen size for a desktop, and a fresh parse is
the only path that does all of that consistently. The row comes back through
Step 2 on the next run, one LLM call each.
"""

import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from src.database.db_manager import DBManager, Deal
from src.models.mac_spec import device_class
from src.utils.chip_extract import DESKTOP_PRODUCT_SERIES, detect_product


def _mismatch(title: str, parsed: dict) -> str | None:
    """Why the stored series disagrees with the title, or None if it does not."""
    product = detect_product(title or "")
    stored = parsed.get("series")
    if product in DESKTOP_PRODUCT_SERIES:
        want = DESKTOP_PRODUCT_SERIES[product]
        if stored != want:
            return f"title says {want}, stored series is {stored!r}"
    elif product == "macbook" and device_class(stored) == "desktop":
        return f"title says MacBook, stored series is {stored!r}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    db = DBManager()
    with db.Session() as session:
        rows = session.query(Deal).filter(Deal.parsed_json.isnot(None)).all()

        failures = []
        for row in rows:
            try:
                parsed = json.loads(row.parsed_json)
            except (TypeError, ValueError):
                continue
            if (why := _mismatch(row.title or "", parsed)):
                failures.append((row, why))

        print(f"checked {len(rows)} parsed rows — {len(failures)} carry the wrong series\n")
        for row, why in failures:
            print(f"  {(row.title or '')[:58]:<58}  {why}")

        if not failures:
            return 0
        if args.apply:
            for row, _ in failures:
                row.parsed_json = None
            session.commit()
            print(f"\ncleared parsed data on {len(failures)} row(s); the next run re-parses them")
        else:
            print("\ndry run — re-run with --apply to clear them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
