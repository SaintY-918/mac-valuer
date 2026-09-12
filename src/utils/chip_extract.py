"""Which Apple Silicon chip a listing title is talking about.

This lived in src/main.py, where the scrapers could not reach it: main.py
imports them, so importing back would be a cycle. PTT kept its own copy of the
idea instead — `_M_CHIPS = ["m1", "m2", "m3", "m4"]` — and that copy went stale
in exactly the way this one already had, dropping every M5 and A-series listing
before it reached the pipeline. Same bug, third appearance, second location.

Everything here is a pure function of a title string, so it is testable without
a network, a database, or a key.
"""

import re

# Matches any Apple Silicon generation rather than a hardcoded list. The list
# stopped at M4, so every M5 listing came back with no chip and was discarded by
# the filter below — silently losing the newest and priciest machines, which are
# exactly the ones worth tracking. A regex means the next generation needs only
# a benchmark entry, not a code change here.
# Two families: the M-series, and the A-series that arrived with the MacBook
# Neo. "A18 Pro" is a real Mac chip, and a title carrying one used to yield no
# chip at all, which meant the listing was discarded outright.
# Boundaries are spelled out rather than using \b, which is Unicode-aware and
# counts CJK as word characters. "M2晶片" therefore had no boundary after the 2
# and never matched at all — and Chinese sellers write it exactly that way, so
# the regex fallback was useless for most Shopee titles and every one of them
# depended on the LLM having succeeded.
#
# Requiring the next character not to be alphanumeric still rejects Apple's
# model identifiers, which are A plus four digits (A1706, A2338).
CHIP_RE = re.compile(
    r"(?<![A-Za-z0-9])([MA])(\d{1,2})\s*(PRO|MAX|ULTRA)?(?![A-Za-z0-9])", re.I)


# Intel's Core M line was m3 / m5 / m7, which collides head-on with Apple's
# M3 / M5. A 2016 12" Retina MacBook with a "Core m5 1.2G" was read as an Apple
# M5, given that chip's 17,933 benchmark, and scored 1060 — top of the whole
# site and well past the alert threshold. An advertised clock speed is the other
# tell: Apple does not market Apple Silicon by GHz, and RAM and storage are
# written "8G/256G", never "1.2G".
#
# "i7/" counts too: the i-series marker used to require a space or hyphen
# after the digit, and "mac mini m1 2012 2014/i7/16g/500g ssd+ 4tb" — one shop
# listing covering several machines, priced from the oldest — slipped past it
# and became an M1 at NT$5,000, top of the whole site.
INTEL_MARKERS = re.compile(
    r"\bintel\b|\bcore\s*[mi]\b|\bi[3579](?=[\s\-/,，、]|$)|\b\d\.\d\s*G(Hz)?\b", re.I)

# Apple Silicon starts with the November 2020 M1. A listing dated earlier cannot
# have one, whatever its title says.
APPLE_SILICON_FIRST_YEAR = 2020

# A model year from before Apple Silicon, written in the title. The parsed
# release_year already vetoes the chip (main.py), but the year can be inferred
# from the chip and overwrite what the seller wrote — so the same veto has to
# read the title directly. Bounded on both sides like CHIP_RE, so Apple's
# A2338-style model numbers and "保固至2025" do not match.
PRE_SILICON_YEAR = re.compile(r"(?<![A-Za-z0-9])(200[6-9]|201[0-9])(?![A-Za-z0-9])")


# A model name that identifies its chip on its own, for titles that never name
# one. Sellers write "MacBook Neo 8G/256GB" because the model has shipped with
# exactly one chip, so naming it would be redundant to them -- seven listings in
# a single run were discarded for this, across three different sellers.
#
# PRODUCT FACT WITH AN EXPIRY DATE. True as of 2026-08: MacBook Neo ships only
# with the A18 Pro. The next Neo generation makes it false, and this table is
# where it breaks -- an unedited entry would label a new machine A18 Pro and
# score it against a benchmark from the wrong silicon, which is worse than the
# missing value it replaced (decisions #9: prefer a gap to a guess).
#
# When a second Neo generation ships, this mapping must either learn to tell
# them apart or be deleted. Stored rows do not re-derive themselves; run
# src/scripts/revalidate_chips.py afterwards (decisions #23).
#
# Matched with \s* because "MacbookNeo" appears in real titles.
_MODEL_CHIPS = (
    (re.compile(r"macbook\s*neo", re.I), "A18 Pro"),
)


def force_extract_chip(title: str) -> str | None:
    """Best chip found in the title, preferring the highest tier mentioned.

    Sellers pad titles with keywords, so "M1 Pro Max" turns up even though no
    such chip exists. Only the variant adjacent to the generation counts, which
    reads that as M1 Pro — the lower tier. That is the safe direction: a lower
    benchmark understates VFM, and an overstated one would fire a false
    bargain alert.

    Returns None for an Intel machine rather than guessing: this project scores
    Apple Silicon, and CHIP_BENCHMARKS has no Intel entries to score against.

    A title that names an Apple chip *and* an Intel-era signal (an i5/i7, a
    2012 model year) is a mixed listing — typically a shop's "現貨" post that
    sells several generations under one title, priced from the cheapest. There
    is no single machine to score, so it is dropped rather than scored as the
    best machine at the worst machine's price.
    """
    if INTEL_MARKERS.search(title) or PRE_SILICON_YEAR.search(title):
        return None
    best = None
    for family, gen, variant in CHIP_RE.findall(title.upper()):
        name = f"{family.upper()}{int(gen)}" + (f" {variant.title()}" if variant else "")
        # "M4 Max" beats a bare "M4" in the same title; higher generations win.
        # A-series sorts below every M-series rather than by number, or A18
        # would outrank an M5 on the digits alone.
        # Apple ships exactly one A-series Mac chip, so a bare "A18" in a
        # MacBook title is the A18 Pro. Left alone it misses the benchmark
        # table and takes the 5,000 fallback, halving the score of a machine
        # whose seller simply did not type "Pro".
        if family.upper() == "A" and int(gen) == 18 and not variant:
            name = "A18 Pro"
        rank = (0 if family.upper() == "A" else 1,
                int(gen), {"": 0, "PRO": 1, "MAX": 2, "ULTRA": 3}[variant.upper()])
        if best is None or rank > best[0]:
            best = (rank, name)
    if best is not None:
        return best[1]
    # Only consulted when the title named no chip at all: an explicit chip
    # always wins, so a title that says both cannot be overridden by the model
    # name. That ordering is what keeps this from becoming a guess.
    for pattern, chip in _MODEL_CHIPS:
        if pattern.search(title):
            return chip
    return None



INVALID_CHIPS = {"unknown", "none", "null", "n/a", ""}


# Which Mac a title is selling. One pattern for all three scrapers: PTT,
# Carousell and Shopee each kept their own "macbook" substring test, and the
# first desktop would have needed the same edit in three places.
#
# A leading boundary only, spelled out rather than \b, because CJK counts as a
# word character and "Macmini賣" would otherwise never match. No trailing
# boundary: sellers write "MacbookPro" and "MacBookAir" as one word, and the
# old substring test accepted those. "iPad mini" has no "mac" before "mini",
# and "Studio Display" on its own is a monitor with no "mac" before "studio",
# so neither matches.
_PRODUCT_PATTERNS = (
    ("macbook",    re.compile(r"(?<![A-Za-z0-9])mac\s*book", re.I)),
    ("mac mini",   re.compile(r"(?<![A-Za-z0-9])mac\s*mini", re.I)),
    ("mac studio", re.compile(r"(?<![A-Za-z0-9])mac\s*studio", re.I)),
)

PRODUCT_NAMES = tuple(name for name, _ in _PRODUCT_PATTERNS)

# The series a desktop product name maps to. Laptops are not here: their
# series (Air / Pro 13 / Pro 14/16 / Neo) needs the screen size as well, and
# the parser already handles that.
DESKTOP_PRODUCT_SERIES = {"mac mini": "Mac mini", "mac studio": "Mac Studio"}


def detect_product(title: str) -> str | None:
    """The Mac named in a title — "macbook", "mac mini", "mac studio" — or None.

    A title naming several ("Mac Studio 含 Studio Display" names one; "MacBook
    換 Mac mini" names two) yields whichever appears first in the text: that is
    the thing being sold, the rest is context.
    """
    text = title or ""
    best: tuple[int, str] | None = None
    for name, pattern in _PRODUCT_PATTERNS:
        m = pattern.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else None


def mentions_apple_silicon(title: str) -> bool:
    """Whether a title names a chip this project can score.

    The scrapers' filters ask this instead of matching a list of generation
    names. A list has to be edited every autumn and silently drops the newest,
    priciest machines when it is not.
    """
    return force_extract_chip(title or "") is not None
