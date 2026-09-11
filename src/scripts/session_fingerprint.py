"""Print a session file's shape without printing the session itself.

    python -m src.scripts.session_fingerprint shopee_state.json

Used around the Shopee CI probe to answer "did the run rotate or invalidate
the cookies?" without ever surfacing the cookies themselves — run once right
after restoring the session and once after the probe, then diff the two
hashes by eye in the run log. Replaces uploading shopee_state.json as a build
artifact: that round-tripped a live login credential into a public repo's
Actions artifacts (downloadable by anyone for the retention window), which is
a live credential leak with extra steps, not a safer version of the same
information.
"""

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m src.scripts.session_fingerprint <path>")
        return 2

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"no session at {path} (nothing to fingerprint)")
        return 0

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:12]

    try:
        data = json.loads(raw)
        cookies = {c.get("name"): c.get("expires") for c in data.get("cookies", [])}
    except (json.JSONDecodeError, AttributeError):
        print(f"{path}: {len(raw):,} B, sha256={digest} (not parseable as Playwright state)")
        return 1

    names = ", ".join(sorted(n for n in cookies if n))
    print(f"{path}: {len(raw):,} B, sha256={digest}, {len(cookies)} cookies ({names})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
