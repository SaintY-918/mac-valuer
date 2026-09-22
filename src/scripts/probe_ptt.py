"""Measure what PTT rejects from a GitHub runner, instead of guessing at it.

    python -m src.scripts.probe_ptt

The 2026-09-22 scheduled run failed with `403 Forbidden` on the very first
request, https://www.ptt.cc/bbs/MacShop/index.html, while the identical request
from a home connection returned 200. Before that night CI read PTT's Atom feed
and article pages without trouble, so three explanations fit:

  1. the index path — PTT closes board listings to datacenter IPs but not the
     feed or articles; the fix is to find the listing somewhere else
  2. the caller's IP — all of www.ptt.cc is closed to this runner now; PTT has
     to leave CI and run from the residential machine like Shopee and Carousell
  3. the caller's headers — nothing but a UA claiming Chrome 120; the fix is to
     send what passes

So this fetches each kind of page PTT serves, then the index under a handful of
header sets. Run it on a GitHub runner (workflow ptt-probe.yml) and compare with
a home connection.

Read-only GETs of public pages, a few seconds apart. No login, no cookies.
"""

import sys
import time

import requests

INDEX = "https://www.ptt.cc/bbs/MacShop/index.html"

CHROME_120 = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CHROME_CURRENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# Scope first: which of PTT's pages does this caller still get?
PATHS = {
    "board index (fails)": INDEX,
    "older index page": "https://www.ptt.cc/bbs/MacShop/index1.html",
    "atom feed (old source)": "https://www.ptt.cc/atom/MacShop.xml",
    # An article is what the detail fetch and the revisit step read. If this
    # passes and the index does not, the scraper can keep running here. A pinned
    # board-rules post (2024-07), because a seller's post can be deleted any day.
    "article page": "https://www.ptt.cc/bbs/MacShop/M.1720087902.A.504.html",
    "site root": "https://www.ptt.cc/bbs/index.html",
}

VARIANTS = {
    # What the scraper sends today, so the probe reproduces the failure first.
    "current scraper": {"User-Agent": CHROME_120},
    "recent chrome UA": {"User-Agent": CHROME_CURRENT},
    "full browser headers": {
        "User-Agent": CHROME_CURRENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    },
    "honest crawler UA": {
        "User-Agent": "mac-valuer/1.0 (+https://github.com/SaintY-918/mac-valuer)",
    },
    "no headers at all": {},
}

# Index row, article body, feed entry, board list on the site root.
_PTT_MARKERS = ('class="title"', 'id="main-content"', "<entry>", 'class="b-ent"')


def _get(url: str, headers: dict) -> tuple:
    try:
        r = requests.get(url, headers=headers, timeout=30)
        # A 200 carrying a challenge page is still a block, just a quieter one;
        # a real listing or article always has at least one of these.
        if not r.ok:
            return r.status_code, r.reason
        if any(m in r.text for m in _PTT_MARKERS):
            return r.status_code, f"{len(r.content):,} bytes"
        return f"{r.status_code}?", f"{len(r.content):,} bytes, NOT a PTT page"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def main() -> int:
    print("--- by path, using the scraper's own headers ---")
    reachable = []
    for name, url in PATHS.items():
        code, note = _get(url, VARIANTS["current scraper"])
        print(f"  {name:<24} {code}  {note}")
        if code == 200:
            reachable.append(name)
        time.sleep(3)
    print()
    print("all of www.ptt.cc is closed to this caller." if not reachable
          else "reachable from here: " + ", ".join(reachable))
    print()

    print(f"--- index under each header set: {INDEX} ---")
    passed = []
    for name, headers in VARIANTS.items():
        code, note = _get(INDEX, headers)
        print(f"  {name:<24} {code}  {note}")
        if code == 200:
            passed.append(name)
        time.sleep(3)
    print()
    if not passed:
        print("no header set gets the index: the block is on the caller, not the headers.")
    elif len(passed) == len(VARIANTS):
        print("every header set gets the index: this connection is not blocked at all.")
    else:
        print("headers matter here. what passed: " + ", ".join(passed))
    # Always exits 0 — a 403 is the measurement, not a failure of the probe.
    return 0


if __name__ == "__main__":
    sys.exit(main())
