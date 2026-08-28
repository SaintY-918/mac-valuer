"""Chip identification and the VFM formula.

Both have produced silent, high-impact failures: a hardcoded generation list
discarded every M5 listing, and two copies of the formula scored the same
machine differently on the page and in the alert.
"""

import pytest

from src.calculator.score_engine import (
    ScoringWeights,
    adjusted_score,
    depreciation,
    form_factor_key,
    get_vfm_score,
    vfm_from_mapping,
)
from src.main import force_extract_chip
from src.models.mac_spec import MacBookSpec
from src.utils.benchmark_db import CHIP_BENCHMARKS, get_benchmark

# ── Chip extraction ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("title, expected", [
    ("MacBook Air M4 13吋",                      "M4"),
    ("MacBook Pro M4 Pro 14吋",                  "M4 Pro"),
    ("MacBook Pro M3 Max 16吋",                  "M3 Max"),
    ("Mac Studio M2 Ultra",                     "M2 Ultra"),
    ("【艾爾巴二手】MACBOOK PRO M3 8G 512G A2918",  "M3"),
])
def test_reads_the_chip_and_its_tier(title, expected):
    assert force_extract_chip(title) == expected


@pytest.mark.parametrize("title, expected", [
    ('最新 M5 Macbook Pro 14" 2026 銀色 16G Ram / 1TB', "M5"),
    ("M6 MacBook Air 2027",                            "M6"),
])
def test_recognises_generations_beyond_the_ones_shipped_today(title, expected):
    """The list used to stop at M4, so every M5 listing came back chipless — and
    chipless listings are discarded, losing the newest and priciest machines."""
    assert force_extract_chip(title) == expected


def test_impossible_chip_names_read_low_not_high():
    """Sellers pad titles with keywords; "M1 Pro Max" is not a product. Reading
    it low understates VFM, which costs a missed opportunity. Reading it high
    would fire a false bargain alert."""
    assert force_extract_chip("Apple MacBook Pro 16吋 M1 Pro Max 2021") == "M1 Pro"


def test_intel_machines_yield_no_apple_silicon_chip():
    assert force_extract_chip("MacBook Air 2020 i5 8G 512G") is None


# ── Benchmarks ────────────────────────────────────────────────────────────────

def test_every_chip_the_extractor_can_produce_has_a_benchmark():
    """A chip without an entry silently scores 5000, far below any real machine."""
    for name in ["M1", "M1 Pro", "M1 Max", "M2", "M2 Pro", "M2 Max",
                 "M3", "M3 Pro", "M3 Max", "M4", "M4 Pro", "M4 Max",
                 "M5", "M5 Pro", "M5 Max"]:
        assert get_benchmark(name) > 5000, f"{name} has no benchmark entry"


def test_benchmarks_rise_with_generation():
    """A sanity check on the table itself: a newer base chip should not score
    below an older one. Catches a typo'd digit."""
    base = [CHIP_BENCHMARKS[f"M{i}"] for i in range(1, 6)]
    assert base == sorted(base), f"base-chip benchmarks are not ascending: {base}"


# ── Form factor ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("series, inches, expected", [
    ("Air",        13.6, "air13"),
    ("Air",        15.0, "air15"),
    ("Pro 13",     13.3, "pro13"),
    ("Pro 14/16",  14.0, "pro14"),
    ("Pro 14/16",  16.0, "pro16"),
])
def test_form_factor_needs_both_series_and_screen_size(series, inches, expected):
    assert form_factor_key(series, inches) == expected


@pytest.mark.parametrize("bad", [None, "", float("nan")])
def test_form_factor_survives_missing_screen_size(bad):
    assert form_factor_key("Air", bad) == "air13"


# ── The formula ───────────────────────────────────────────────────────────────

def _spec(**kw):
    base = dict(chip="M4", ram_gb=16, ssd_gb=512, screen_size=13.6,
                release_year=2025, series="Air", price=30000.0)
    base.update(kw)
    return MacBookSpec(**base)


def test_score_is_benchmark_points_per_thousand_dollars():
    spec = _spec()
    weights = ScoringWeights()
    expected = adjusted_score("M4", 16, 512, 13.6, "Air", 2025, weights) / 30000 * 1000
    assert get_vfm_score(spec, weights) == pytest.approx(expected)


def test_a_cheaper_identical_machine_scores_higher():
    assert get_vfm_score(_spec(price=20000.0)) > get_vfm_score(_spec(price=30000.0))


def test_no_price_means_no_score():
    assert get_vfm_score(_spec(price=None)) == 0.0
    assert get_vfm_score(_spec(price=0)) == 0.0


def test_bonuses_apply_only_at_their_thresholds():
    assert get_vfm_score(_spec(ram_gb=16)) > get_vfm_score(_spec(ram_gb=8))
    assert get_vfm_score(_spec(ssd_gb=1024)) > get_vfm_score(_spec(ssd_gb=512))
    # Below the threshold, size makes no difference
    assert get_vfm_score(_spec(ssd_gb=512)) == get_vfm_score(_spec(ssd_gb=256))


def test_older_machines_depreciate():
    assert depreciation(2020) < depreciation(2025)


def test_an_unknown_year_is_treated_as_current():
    """Falling back to an old year halved the score of brand-new machines: two
    copies of one listing scored 432 and 204 on that difference alone."""
    assert depreciation(None) == 1.0


# ── The two entry points must agree ───────────────────────────────────────────

@pytest.mark.parametrize("row", [
    {"chip": "M4", "ram_gb": 16, "ssd_gb": 512, "screen_size": 15.0,
     "series": "Air", "release_year": 2025, "price": 30000},
    {"chip": "M1 Pro", "ram_gb": 16, "ssd_gb": 1024, "screen_size": 16.0,
     "series": "Pro 14/16", "release_year": 2021, "price": 19000},
    {"chip": "M5", "ram_gb": 32, "ssd_gb": 4096, "screen_size": 14.0,
     "series": "Pro 14/16", "release_year": 2026, "price": 88000},
])
def test_mapping_and_spec_paths_give_the_same_score(row):
    """The dashboard used to carry its own copy of this formula. They drifted
    until 59 of 125 listings disagreed and 7 sat on opposite sides of the alert
    threshold — shown as excellent but never alerted, or vice versa."""
    weights = ScoringWeights()
    from_spec = get_vfm_score(MacBookSpec(**row), weights)
    from_row = vfm_from_mapping(row, weights)
    assert from_row == pytest.approx(from_spec, abs=0.01)


def test_nan_in_a_row_does_not_crash_the_score():
    """DataFrame cells arrive as NaN, which is a float and truthy."""
    nan = float("nan")
    score = vfm_from_mapping({"chip": "M4", "ram_gb": nan, "ssd_gb": nan,
                              "screen_size": nan, "series": "Air",
                              "release_year": nan, "price": 30000})
    assert score > 0


# ── MacBook Neo / A18 Pro ─────────────────────────────────────────────────────
# The first Mac on an iPhone chip (March 2026). Before it was added the chip
# regex matched only M-series, so force_extract_chip returned None and main.py
# discarded the listing as having no chip at all.

def test_a18_pro_is_extracted_from_a_title():
    assert force_extract_chip("MacBook Neo A18 Pro 13吋 8G/256G 2026") == "A18 Pro"


def test_bare_a18_is_read_as_the_only_a_series_mac_chip():
    """Apple ships one. A seller who omits "Pro" should not lose half the score."""
    assert force_extract_chip("MacBook Neo A18 8G/256G") == "A18 Pro"


def test_m_series_still_wins_over_a_series_in_the_same_title():
    """A18 must not outrank M5 on the digits alone."""
    assert force_extract_chip("MacBook Pro M5 Max 比 A18 Pro 快") == "M5 Max"
    assert force_extract_chip("MacBook Pro M4 Max 對比 A18 Pro") == "M4 Max"


def test_a18_pro_has_a_benchmark():
    """Without this the Neo takes the unknown-chip fallback of 5000."""
    assert get_benchmark("A18 Pro") == 8668
    assert get_benchmark("A18 Pro") > get_benchmark("M1")


def test_neo_is_scored_as_a_13_inch_air():
    """A fanless 13" entry machine, whatever Apple calls it."""
    assert form_factor_key("Neo", 13) == "air13"
    assert form_factor_key("MacBook Neo", 13.6) == "air13"


def test_the_a_series_regex_does_not_match_ordinary_title_text():
    """Titles are full of stray letters and numbers; A-matching must stay tight."""
    assert force_extract_chip("MacBook Air 13 2020 8G/256G") is None
    assert force_extract_chip("MacBook Pro 13吋 A1706 鍵盤") != "A17 Pro"


# ── Intel Core M collides with Apple M ────────────────────────────────────────
# Intel's Core M line was m3/m5/m7. A 2016 12" Retina MacBook advertising
# "Core m5 1.2G" was read as an Apple M5, handed that chip's 17,933 benchmark,
# and scored 1060 — the top listing on the site, past the alert threshold.

def test_an_intel_core_m_is_not_read_as_an_apple_chip():
    title = "（Apple蘋果）超輕薄MacBook Retina 12吋 M5  1.2G 處理器 8GB 記憶體 512G"
    assert force_extract_chip(title) is None


@pytest.mark.parametrize("title", [
    "MacBook Pro 13吋 Intel i5 8G/256G",
    "MacBook Air 2017 Core i7 8G",
    "MacBook 12吋 Core m3 1.1GHz",
    "MacBook Pro 15 i9-9880H 16G",
])
def test_intel_machines_yield_no_chip(title):
    """No Intel entries exist in CHIP_BENCHMARKS, so a guess would score against
    the 5000 fallback rather than anything real."""
    assert force_extract_chip(title) is None


@pytest.mark.parametrize("title", [
    "[販售] MacBook Air 13 M1 8G/256G 2020",
    "MacBook Pro 14 M3 Pro 18G/512G",
    "MacBook Air 15 M4 16G/512G 2025",
    "MacBook Neo A18 Pro 8G/256G",
    # Storage and memory are written without a decimal point, so the clock-speed
    # rule must not catch them.
    "MacBook Pro 16 M4 Max 48G/1T",
])
def test_apple_silicon_still_extracts(title):
    assert force_extract_chip(title) is not None


# ── CJK titles ────────────────────────────────────────────────────────────────
# \b is Unicode-aware and treats CJK as word characters, so "M2晶片" had no
# boundary after the 2 and never matched. Chinese sellers write it exactly that
# way, which left the regex fallback useless for most Shopee titles — every one
# of them relied on the LLM having succeeded.

@pytest.mark.parametrize("title,expected", [
    ("Apple MacBook Air Retina 15 吋 M2晶片 2023 蘋果筆電", "M2"),
    ("Macbook air 15吋 m4晶片 16GB 256GB 極新二手", "M4"),
    ("蘋果M3 Pro晶片 14吋", "M3 Pro"),
    ("MacBook Pro 16吋M4 Max晶片", "M4 Max"),
])
def test_a_chip_written_against_chinese_text_is_found(title, expected):
    assert force_extract_chip(title) == expected


@pytest.mark.parametrize("title", [
    "MacBook Pro 13吋 A1706 鍵盤更換",
    "MacBook Air A1932 外殼",
])
def test_apple_model_identifiers_are_still_rejected(title):
    """A plus four digits is a model number, not an A-series chip."""
    assert force_extract_chip(title) is None


def test_a_chip_token_inside_a_word_is_not_matched():
    """HDMI1 and similar must not read as M1."""
    assert force_extract_chip("MacBook 轉接器 HDMI1 埠") is None
