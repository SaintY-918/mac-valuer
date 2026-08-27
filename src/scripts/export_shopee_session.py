"""Pack shopee_state.json into a value that fits a GitHub Secret.

    python -m src.scripts.export_shopee_session

Playwright's storage_state carries both cookies and `origins` (localStorage).
Only the cookies authenticate; origins is ~117 KB of page state that would push
the blob past the 48 KB secret limit for nothing. Drop it, gzip, base64.

Prints the value to paste into the SHOPEE_STATE_B64 repository secret. The
output is a live login credential — treat it like a password.
"""

import argparse
import base64
import gzip
import json
import sys
from pathlib import Path

GITHUB_SECRET_LIMIT = 48_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="shopee_state.json")
    ap.add_argument("--out", help="write to this file instead of stdout")
    args = ap.parse_args()

    path = Path(args.state)
    if not path.exists():
        print(f"{path} not found. Run once with SHOPEE_HEADLESS=false to log in first.")
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = data.get("cookies") or []
    if not cookies:
        print("No cookies in the state file — the session is empty.")
        return 1

    slim = {"cookies": cookies, "origins": []}
    blob = base64.b64encode(gzip.compress(json.dumps(slim, ensure_ascii=False).encode(), 9)).decode()

    names = {c.get("name") for c in cookies}
    missing = {"SPC_ST", "SPC_U"} - names
    print(f"cookies      : {len(cookies)}", file=sys.stderr)
    print(f"encoded size : {len(blob):,} B (limit {GITHUB_SECRET_LIMIT:,})", file=sys.stderr)
    if missing:
        print(f"WARNING      : login cookies missing: {', '.join(sorted(missing))}", file=sys.stderr)
    if len(blob) >= GITHUB_SECRET_LIMIT:
        print("Too large for one secret.", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(blob, encoding="utf-8")
        print(f"\nWritten to {args.out} — paste its contents into the "
              f"SHOPEE_STATE_B64 secret, then delete the file.", file=sys.stderr)
    else:
        print("\nPaste everything below into the SHOPEE_STATE_B64 secret:\n", file=sys.stderr)
        print(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main())
