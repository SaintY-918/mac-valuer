"""Repair ram_gb / ssd_gb values that are not configurations Apple ships.

    python -m src.scripts.repair_specs            # dry run, reports only
    python -m src.scripts.repair_specs --apply    # write the corrections

A GPU core count read as memory ("8C10G" -> 10 GB RAM, 8 TB SSD) is worse than
a missing value: an 8 TB SSD clears the >=1 TB threshold and inflates the VFM
score. This re-extracts from the title with the corrected regex and patches only
ram_gb / ssd_gb, leaving every other parsed field alone.

Rows the title cannot settle are left untouched rather than guessed at.
"""

import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from src.database.db_manager import DBManager, Deal  # noqa: E402
from src.parser.llm_parser import (  # noqa: E402
    _VALID_RAM, _VALID_SSD, extract_specs_from_text,
)


def _implausible(value, valid: set) -> bool:
    if value in (None, "", "None", 0):
        return False
    try:
        return int(float(value)) not in valid
    except (TypeError, ValueError):
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    db = DBManager()
    with db.Session() as session:
        rows = session.query(Deal).filter(Deal.parsed_json.isnot(None)).all()

        suspect = []
        for row in rows:
            parsed = json.loads(row.parsed_json)
            bad_ram = _implausible(parsed.get("ram_gb"), _VALID_RAM)
            bad_ssd = _implausible(parsed.get("ssd_gb"), _VALID_SSD)
            if bad_ram or bad_ssd:
                suspect.append((row, parsed, bad_ram, bad_ssd))

        print(f"scanned {len(rows)} parsed rows — {len(suspect)} carry an impossible value\n")
        if not suspect:
            return 0

        fixed = skipped = 0
        for row, parsed, bad_ram, bad_ssd in suspect:
            ram, ssd = extract_specs_from_text(row.title or "")
            before = f"RAM={parsed.get('ram_gb')} SSD={parsed.get('ssd_gb')}"

            # RAM and SSD were read from the same string in one pass, so an
            # impossible value in either condemns the pair — the surviving half
            # is not trustworthy just because it happens to be a size Apple
            # sells. "8C10G/8G/256G" yielded RAM=10 and SSD=8192: 8 TB is a real
            # configuration, but not on this 13" Pro, and the title says 256 GB.
            changes = {}
            if ram is not None or ssd is not None:
                changes["ram_gb"] = ram
                changes["ssd_gb"] = ssd
            else:
                # Nothing to replace them with. Clearing beats keeping a number
                # that came from a misread — the scorer falls back to a default
                # instead of trusting it.
                if bad_ram:
                    changes["ram_gb"] = None
                if bad_ssd:
                    changes["ssd_gb"] = None

            after = (f"RAM={changes.get('ram_gb', parsed.get('ram_gb'))} "
                     f"SSD={changes.get('ssd_gb', parsed.get('ssd_gb'))}")
            print(f"  {(row.title or '')[:56]:<56}")
            print(f"    {before}  ->  {after}")

            if not any(v is not None for v in changes.values()):
                skipped += 1
            else:
                fixed += 1

            if args.apply:
                parsed.update(changes)
                row.parsed_json = json.dumps(parsed, ensure_ascii=False)

        if args.apply:
            session.commit()
            print(f"\napplied: {fixed} corrected, {skipped} cleared to unknown")
        else:
            print(f"\ndry run — would correct {fixed}, clear {skipped}")
            print("re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
