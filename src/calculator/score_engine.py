"""VFM (Value For Money) scoring — the single implementation.

The dashboard used to carry its own copy of this formula and its own benchmark
table. The two drifted: 59 of 125 listings scored differently, the largest gap
55 points, because the backend read the form factor from the `series` string
alone while the dashboard also looked at screen size. Seven listings landed on
opposite sides of the Discord alert threshold — shown as "優秀" on the page but
never alerted, or alerted while displayed as merely average.

Everything that computes a VFM score now goes through this module. Callers that
hold a MacBookSpec use `get_vfm_score`; callers holding a raw row (the dashboard,
where values arrive from a DataFrame and may be NaN) use `vfm_from_mapping`.
"""

import datetime
import math
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from src.models.mac_spec import MacBookSpec
from src.utils.benchmark_db import get_benchmark

DEPRECIATION_RATE = 0.10

# Ages are counted in the market's calendar, not the server's. Streamlit Cloud
# and GitHub Actions both run in UTC while the listings are Taiwanese, so around
# New Year a naive now() would put the two eight hours and one whole year of
# depreciation apart.
MARKET_TZ = ZoneInfo("Asia/Taipei")


def current_year() -> int:
    return datetime.datetime.now(MARKET_TZ).year

RAM_BONUS_THRESHOLD_GB = 16
SSD_BONUS_THRESHOLD_GB = 1024


class ScoringWeights(BaseModel):
    """Multipliers applied on top of the chip benchmark.

    Form factors are split by screen size, not just by series: a 15" Air and a
    16" Pro command a premium over their smaller siblings, and collapsing them
    loses that. The dashboard's sliders write straight into these fields.
    """

    ram_multiplier: float = 1.25    # applied when ram_gb >= 16
    ssd_multiplier: float = 1.1     # applied when ssd_gb >= 1024 (1 TB+)

    form_air13: float = 1.00
    form_air15: float = 1.08
    form_pro13: float = 1.00
    form_pro14: float = 1.18
    form_pro16: float = 1.22

    def form_weight(self, key: str) -> float:
        return float(getattr(self, f"form_{key}", 1.0))


# One shared instance rather than a call in each signature. A default argument
# is evaluated once at definition time, so `weights=ScoringWeights()` hands every
# caller the same mutable object — naming it makes that explicit instead of
# accidental.
DEFAULT_WEIGHTS = ScoringWeights()


def form_factor_key(series: Any, screen_size: Any) -> str:
    """Bucket a listing into one of the five form factors.

    Screen size decides within a family, so this needs both fields. Keep it the
    only place that rule lives.
    """
    name = str(series or "").lower()
    try:
        inches = float(screen_size) if screen_size else 13.3
    except (TypeError, ValueError):
        inches = 13.3
    if math.isnan(inches):
        inches = 13.3

    # The MacBook Neo is a 13" fanless entry machine — an Air in everything but
    # the name. Without this it fell through to pro13 by default.
    if "air" in name or "neo" in name:
        return "air15" if inches >= 15.0 else "air13"
    if inches >= 15.0:
        return "pro16"
    if inches >= 14.0:
        return "pro14"
    return "pro13"


# Apple markets four sizes; sellers write seven. The measured diagonal varies
# by generation (an Air 13" is 13.3" on the M1 and 13.6" from the M2 on) and
# gets copied inconsistently, and the live data also held a 15.6" — a Windows
# laptop size Apple has never shipped.
#
# Keyed off form_factor_key rather than re-thresholding the raw number, so the
# size on the card and the multiplier used to score it can never disagree.
FORM_INCHES = {"air13": 13, "air15": 15, "pro13": 13, "pro14": 14, "pro16": 16}


def nominal_inches(series: Any, screen_size: Any) -> int | None:
    """Apple's marketing size for a listing, or None when nothing was parsed.

    None rather than a guess: form_factor_key falls back to 13.3" for a missing
    value, which is the right default for scoring but would state a size on the
    card that nobody actually read.
    """
    if _num(screen_size) <= 0:
        return None
    return FORM_INCHES.get(form_factor_key(series, screen_size))


def depreciation(release_year: Any) -> float:
    """Straight-line-by-year decay. Unknown years are treated as current, since
    guessing old halves the score of a machine that may be new."""
    try:
        year = int(release_year)
    except (TypeError, ValueError):
        return 1.0
    age = max(0, current_year() - year)
    return (1 - DEPRECIATION_RATE) ** age


def _num(value: Any, default: float = 0.0) -> float:
    """DataFrame cells arrive as NaN rather than None, and NaN is truthy."""
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(out) else out


def adjusted_score(
    chip: Any,
    ram_gb: Any,
    ssd_gb: Any,
    screen_size: Any,
    series: Any,
    release_year: Any,
    weights: ScoringWeights,
) -> float:
    """Benchmark points after depreciation and the spec/form multipliers."""
    base = get_benchmark(str(chip or ""))
    ram_mult = weights.ram_multiplier if _num(ram_gb) >= RAM_BONUS_THRESHOLD_GB else 1.0
    ssd_mult = weights.ssd_multiplier if _num(ssd_gb) >= SSD_BONUS_THRESHOLD_GB else 1.0
    form_mult = weights.form_weight(form_factor_key(series, screen_size))
    return base * depreciation(release_year) * ram_mult * ssd_mult * form_mult


def calculate_adjusted_score(spec: MacBookSpec, weights: ScoringWeights) -> float:
    return adjusted_score(
        spec.chip, spec.ram_gb, spec.ssd_gb, spec.screen_size,
        spec.series, spec.release_year, weights,
    )


def get_vfm_score(
    spec: MacBookSpec,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> float:
    """Benchmark points per NT$1,000."""
    if not spec.price or spec.price <= 0:
        return 0.0
    return (calculate_adjusted_score(spec, weights) / spec.price) * 1000


def vfm_from_mapping(
    row: Mapping[str, Any],
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> float:
    """Same score, for callers holding a plain row rather than a MacBookSpec.

    Building a MacBookSpec per row on every Streamlit rerun would mean pydantic
    validation over the whole table, and rows straight from a DataFrame carry
    NaN where a field is missing.
    """
    price = _num(row.get("price"))
    if price <= 0:
        return 0.0
    score = adjusted_score(
        row.get("chip"), row.get("ram_gb"), row.get("ssd_gb"),
        row.get("screen_size"), row.get("series"), row.get("release_year"),
        weights,
    )
    return round(score / price * 1000, 2)
