"""Decode SHOPEE_STATE_B64 back into shopee_state.json.

    SHOPEE_STATE_B64=... python -m src.scripts.restore_shopee_session

Deliberately not a shell one-liner. `base64 -d | gunzip > file` fails with
"invalid input" on any stray whitespace — which a 3 KB blob copied out of a
wrapping terminal reliably picks up — and the redirect creates the output file
before the pipeline runs, so a failed decode still leaves an empty file behind
that looks like success. This strips whitespace, repairs padding, validates the
result, and only writes once the JSON parses.
"""

import base64
import gzip
import json
import os
import sys
from pathlib import Path

REQUIRED_COOKIES = ("SPC_ST", "SPC_U")


def main() -> int:
    blob = os.getenv("SHOPEE_STATE_B64", "")
    if not blob.strip():
        print("SHOPEE_STATE_B64 is empty or unset.")
        print("Generate it with: python -m src.scripts.export_shopee_session --out session.txt")
        return 2

    # Terminals wrap long lines; copying the blob out of one brings newlines,
    # and some editors add a stray space. None of it is part of the value.
    cleaned = "".join(blob.split())
    if len(cleaned) != len(blob):
        print(f"note: stripped {len(blob) - len(cleaned)} whitespace character(s)")

    cleaned += "=" * (-len(cleaned) % 4)  # tolerate stripped padding

    try:
        raw = gzip.decompress(base64.b64decode(cleaned, validate=True))
    # binascii.Error covers malformed base64; a plain ValueError comes from
    # non-ASCII input, which is what a wrong value pasted by hand looks like.
    except ValueError as e:
        print(f"Not valid base64: {e}")
        print(f"Value is {len(cleaned)} chars after cleaning; expected ~3,052.")
        print("Re-copy it — write to a file with --out rather than copying from the terminal.")
        return 1
    except (OSError, EOFError) as e:
        print(f"base64 decoded but gunzip failed: {e}")
        print("The secret is probably truncated.")
        return 1

    try:
        data = json.loads(raw)
        cookies = data["cookies"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Decompressed, but the contents are not a Playwright state file: {e}")
        return 1

    missing = [c for c in REQUIRED_COOKIES if c not in {x.get("name") for x in cookies}]
    if missing:
        print(f"WARNING: login cookies missing: {', '.join(missing)} — the session will not authenticate")

    out = Path(os.getenv("SHOPEE_STATE_PATH", "shopee_state.json"))
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"restored {len(cookies)} cookies to {out} ({out.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
