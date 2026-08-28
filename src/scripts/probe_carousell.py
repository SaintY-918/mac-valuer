"""Measure what Carousell actually rejects, instead of guessing at it.

    python -m src.scripts.probe_carousell

The scheduled run failed with `403 Forbidden` on the sitemap while the identical
request from a home connection returned 200. Two explanations fit that equally
well and they need opposite fixes:

  1. the caller's IP — a datacenter range, nothing header-side will help
  2. the caller's headers — the scraper claims to be Chrome 120, a build from
     December 2023. From a datacenter IP an ancient browser UA is a bot
     signature; a current UA, or an honest crawler UA, may be treated better

So this fetches one public sitemap under a handful of header sets and prints the
status of each. Run it on a GitHub runner and on a home connection, then compare:

  - every variant 403 on the runner and 200 at home  -> IP reputation, and no
    amount of header tuning will fix the scheduled run
  - some variants pass on the runner                 -> headers, and the fix is
                                                        to send those

Read-only GETs of a published sitemap, a few seconds apart. No login, no
cookies, nothing that can be invalidated by running it.
"""

import sys
import time

import requests

TARGET = "https://tw.carousell.com/sitemaps/products/tw-computers-tech.xml"

CHROME_120 = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CHROME_CURRENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

VARIANTS = {
    # What the scraper sends today, so the probe reproduces the failure first.
    "current scraper": {
        "User-Agent": CHROME_120,
        "Accept-Language": "zh-TW,zh;q=0.9",
    },
    # Same shape, believable version. Isolates "your UA is three years stale".
    "recent chrome UA": {
        "User-Agent": CHROME_CURRENT,
        "Accept-Language": "zh-TW,zh;q=0.9",
    },
    # A browser sends more than a UA. If the WAF fingerprints header sets, the
    # bare pair above looks nothing like a real Chrome request.
    "full browser headers": {
        "User-Agent": CHROME_CURRENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        # No "br": requests only decodes brotli when the extra is installed,
        # and a body left compressed makes the size column look like a truncated
        # response. The status code is what this measures anyway.
        "Accept-Encoding": "gzip, deflate",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    },
    # Sitemaps exist for crawlers. Claiming to be one, honestly, is the option
    # that needs no pretending — and it is the one to adopt if it works.
    "honest crawler UA": {
        "User-Agent": "mac-valuer/1.0 (+https://github.com/SaintY-918/mac-valuer)",
        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
    },
    "no headers at all": {},
}

# Measured 2026-08-28: every header variant 403s from a GitHub runner and every
# one returns 200 from a home connection, so headers are settled. What is not
# settled is scope — the probe only ever asked for one URL. If the block covers
# /sitemaps/ but not the rest, the scraper can be restructured around that; if
# it is domain-wide, Carousell cannot run here at all. These separate the two.
PATHS = {
    "sitemap (what fails)": TARGET,
    "sitemap index": "https://tw.carousell.com/sitemap.xml",
    "robots.txt": "https://tw.carousell.com/robots.txt",
    "home page": "https://tw.carousell.com/",
    # A real listing. If product pages are reachable, the scraper could source
    # its URL list elsewhere and still read Carousell from CI.
    "product page": ("https://tw.carousell.com/p/"
                     "macbook-air-m2-15%E5%90%8B-8g-256g-1457068258/"),
}


def probe_paths() -> None:
    """Is the whole domain closed to this caller, or only the sitemap path?"""
    print("--- by path, using the scraper's own headers ---")
    headers = VARIANTS["current scraper"]
    codes = {}
    for name, url in PATHS.items():
        try:
            r = requests.get(url, headers=headers, timeout=30)
            codes[name] = r.status_code
            note = f"{len(r.content):,} bytes" if r.ok else r.reason
            print(f"  {name:<22} {r.status_code}  {note}")
        except Exception as e:
            codes[name] = None
            print(f"  {name:<22} ---  {type(e).__name__}: {e}")
        time.sleep(3)

    reachable = [n for n, c in codes.items() if c == 200]
    print()
    print("the whole domain is closed to this caller." if not reachable
          else "reachable from here: " + ", ".join(reachable))


def main() -> int:
    print(f"target: {TARGET}\n")
    results = {}
    for name, headers in VARIANTS.items():
        try:
            r = requests.get(TARGET, headers=headers, timeout=30)
            results[name] = r.status_code
            # Count the sitemap's own entries: a 200 carrying a challenge page
            # instead of XML is still a block, just a quieter one.
            note = f"{r.text.count('<loc>'):,} urls" if r.ok else r.reason
            print(f"  {name:<22} {r.status_code}  {note}")
        except Exception as e:
            results[name] = None
            print(f"  {name:<22} ---  {type(e).__name__}: {e}")
        time.sleep(3)

    ok = [n for n, code in results.items() if code == 200]
    print()
    if not ok:
        print("nothing got through: the block is on the caller, not the headers.")
    elif len(ok) == len(results):
        print("everything got through: this connection is not being blocked at all.")
    else:
        print("headers matter here. what passed: " + ", ".join(ok))
    print()

    probe_paths()
    # Always exits 0 — a 403 is the measurement, not a failure of the probe.
    return 0


if __name__ == "__main__":
    sys.exit(main())
