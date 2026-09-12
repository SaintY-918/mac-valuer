"""Re-run defect detection over every stored row with the current rules.

    python -m src.scripts.revalidate_defects            # dry run, reports only
    python -m src.scripts.revalidate_defects --apply    # write the new lists

Defects are detected once, at parse time, and stored in parsed_json — the body
is only in hand then (api/spec.md 4.X.5). So when the detector's rules change,
the stored verdicts do not (decisions #23). A sealed MacBook Neo stayed flagged
瑕疵 after the grading-legend rule shipped, because nothing re-read its body.

This rewrites only the `defects` key, through update_parsed so last_seen does
not move: re-reading stored text is not a sighting.
"""

import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from src.database.db_manager import DBManager, Deal
from src.parser.condition_flags import find_defects


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    db = DBManager()
    with db.Session() as session:
        rows = session.query(Deal).filter(Deal.parsed_json.isnot(None)).all()
        changes = []
        for row in rows:
            try:
                parsed = json.loads(row.parsed_json)
            except (TypeError, ValueError):
                continue
            if "defects" not in parsed:
                # Never scanned with the body in hand; the read paths fall back
                # to title and condition for these, and that stays true.
                continue
            now = find_defects(row.title, parsed.get("condition"), row.body_content)
            if now != parsed["defects"]:
                changes.append((row, parsed, now))

    print(f"checked {len(rows)} parsed rows — {len(changes)} verdict(s) would change\n")
    for row, parsed, now in changes:
        print(f"  {(row.title or '')[:56]:<56}  {parsed['defects']} -> {now}")

    if not changes:
        return 0
    if args.apply:
        for row, parsed, now in changes:
            db.update_parsed(row.url, {**parsed, "defects": now})
        print(f"\nrewrote defects on {len(changes)} row(s)")
    else:
        print("\ndry run — re-run with --apply to write them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
