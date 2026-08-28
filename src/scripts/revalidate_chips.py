"""Re-check every stored chip against the current extraction rules.

    python -m src.scripts.revalidate_chips            # dry run, reports only
    python -m src.scripts.revalidate_chips --apply    # clear the failures

The rules that decide whether a listing is an Apple Silicon Mac change as the
collisions turn up — Intel's Core m3/m5/m7 against Apple's M3/M5 was the first
— but rows already parsed keep whatever they were given. A 2016 Core m5 sat at
the top of the site scoring 1060 for exactly that reason: the fix stopped new
listings being misread and did nothing about the one already stored.

Failures have their parsed_json cleared rather than the row deleted. The
listing genuinely exists; it is simply out of scope, and a row with no parsed
spec is already how the pipeline represents that — every read path requires a
non-null parsed_json.
"""

import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from src.database.db_manager import DBManager, Deal
from src.main import _INTEL_MARKERS, _INVALID_CHIPS, APPLE_SILICON_FIRST_YEAR


def _rejection(title: str, parsed: dict) -> str | None:
    """Why this row would be rejected today, or None if it still passes."""
    chip = parsed.get("chip")
    if chip is None or str(chip).strip().lower() in _INVALID_CHIPS:
        return "no chip"
    if _INTEL_MARKERS.search(title or ""):
        return f"Intel markers in title (stored chip {chip})"
    year = parsed.get("release_year")
    try:
        if year and int(year) < APPLE_SILICON_FIRST_YEAR:
            return f"{chip} dated {year}, before Apple Silicon"
    except (TypeError, ValueError):
        pass
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
            if (why := _rejection(row.title or "", parsed)):
                failures.append((row, why))

        print(f"checked {len(rows)} parsed rows — {len(failures)} no longer pass\n")
        for row, why in failures:
            print(f"  {(row.title or '')[:58]:<58}  {why}")

        if not failures:
            return 0
        if args.apply:
            for row, _ in failures:
                row.parsed_json = None
            session.commit()
            print(f"\ncleared parsed data on {len(failures)} row(s)")
        else:
            print("\ndry run — re-run with --apply to clear them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
