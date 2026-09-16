"""Chip identification and the VFM formula.

Both have produced silent, high-impact failures: a hardcoded generation list
discarded every M5 listing, and two copies of the formula scored the same
machine differently on the page and in the alert.
"""

import pytest

from src.calculator.score_engine import (
    FAMILY_INCHES,
    FORM_INCHES,
    ScoringWeights,
    adjusted_score,
    depreciation,
    form_factor_key,
    get_vfm_score,
    nominal_inches,
    vfm_from_mapping,
)
from src.main import _is_intel_era_row
from src.models.mac_spec import MacBookSpec, device_class
from src.utils.benchmark_db import CHIP_BENCHMARKS, get_benchmark
from src.utils.chip_extract import (
    detect_product,
    force_extract_chip,
    is_intel_era_title,
)

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


# ── Screen size: four marketing sizes, seven written forms ────────────────────
# The measured diagonal changes between generations (an Air 13" is 13.3" on the
# M1 and 13.6" from the M2 on) and sellers copy whichever they saw. The live
# database held 13.0, 13.3, 13.6, 14.0, 14.2, 15.0, 15.6 and 16.2 — and 15.6 is
# a Windows laptop size Apple has never shipped.

@pytest.mark.parametrize("series,raw,expected", [
    ("Air", 13.0, 13), ("Air", 13.3, 13), ("Air", 13.6, 13),
    ("Air", 15.0, 15), ("Air", 15.3, 15),
    ("Pro 13", 13.3, 13),
    ("Pro 14/16", 14.0, 14), ("Pro 14/16", 14.2, 14),
    ("Pro 14/16", 16.0, 16), ("Pro 14/16", 16.2, 16),
    ("Neo", 13, 13),
])
def test_screen_sizes_collapse_to_apple_marketing_sizes(series, raw, expected):
    assert nominal_inches(series, raw) == expected


def test_a_size_apple_never_made_still_lands_somewhere_sensible():
    """15.6" is a Windows size. One listing claimed it; it is a 15" Air."""
    assert nominal_inches("Air", 15.6) == 15


@pytest.mark.parametrize("raw", [None, 0, ""])
def test_an_unparsed_size_is_not_guessed(raw):
    """form_factor_key defaults to 13.3 for scoring, which is the right
    fallback for a multiplier and the wrong thing to print on a card."""
    assert nominal_inches("Air", raw) is None


def test_the_displayed_size_agrees_with_the_scored_form_factor():
    """Both come from form_factor_key, so they cannot drift apart."""
    for series, raw in [("Air", 13.6), ("Air", 15.3), ("Pro 14/16", 14.2), ("Pro 14/16", 16.2)]:
        key = form_factor_key(series, raw)
        assert FORM_INCHES[key] == nominal_inches(series, raw)


def test_every_family_offers_only_sizes_it_is_sold_in():
    """Apple has never made a 15" Pro or a 14" Air. Offering the combination
    returns an empty list, which reads as missing stock rather than an
    impossible request."""
    assert 15 not in FAMILY_INCHES["Pro"]
    assert 14 not in FAMILY_INCHES["Air"]
    assert 16 not in FAMILY_INCHES["Air"]
    assert FAMILY_INCHES["Neo"] == (13,)


def test_the_offered_sizes_are_ones_the_scorer_recognises():
    """A size in the filter that form_factor_key maps elsewhere would let
    someone select a combination the scoring cannot represent."""
    for family, sizes in FAMILY_INCHES.items():
        series = {"Air": "Air", "Pro": "Pro 14/16", "Neo": "Neo"}[family]
        for inches in sizes:
            # Pro 13 is its own series value; check via the family's own bucket.
            key = form_factor_key(series if inches > 13 else f"{family} 13", inches)
            assert FORM_INCHES[key] == inches, (family, inches, key)


# ── a model name that identifies its own chip ─────────────────────────────────

def test_a_neo_that_never_names_its_chip_is_still_a_neo():
    """Sellers write "MacBook Neo 8G/256GB" and stop there.

    The model has shipped with exactly one chip, so naming it is redundant to
    them. Seven listings in one run were discarded for this.
    """
    assert force_extract_chip("Macbook neo 512GB") == "A18 Pro"
    assert force_extract_chip("二手 MacBook Neo 256GB 藍色") == "A18 Pro"


def test_the_model_name_survives_being_written_without_a_space():
    assert force_extract_chip("【售】僅拆封測試!!!極新MacbookNeo 256G 粉色") == "A18 Pro"


def test_an_explicit_chip_always_beats_the_model_name():
    """This ordering is what keeps the mapping from being a guess.

    When the next Neo generation ships with different silicon, a seller who
    names that chip must not be overridden by a table written in 2026.
    """
    assert force_extract_chip("MacBook Neo M4 測試") == "M4"


def test_an_intel_marker_still_wins_over_the_model_name():
    assert force_extract_chip("Intel MacBook Neo") is None


# ── Desktops: Mac mini and Mac Studio ─────────────────────────────────────────
# One class of thing — a box with no screen and no battery — that differs
# within itself the way an Air differs from a Pro. Everything that used to
# assume "a Mac is a laptop" is checked here.

@pytest.mark.parametrize("title, expected", [
    ("Mac mini M4 16G/256G",              "mac mini"),
    ("[賣機] Mac mini M2 Pro 16G/512G",    "mac mini"),
    ("Macmini賣 M1",                       "mac mini"),      # CJK right after, no space
    ("MAC STUDIO M2 Ultra 64G/1TB",       "mac studio"),
    ("Mac Studio 含 Studio Display",       "mac studio"),    # the monitor is context
    ("MacbookPro 14 M3",                  "macbook"),       # one word, as sellers write it
    ("MacBook 換 Mac mini",               "macbook"),       # first named wins
    ("iPad mini 6 64G",                   None),            # not a Mac
    ("Studio Display 27吋",                None),            # a monitor
    ("iMac 24 M1",                        None),            # deferred, see decisions
    ("Windows 筆電 類macbook 外型",         "macbook"),       # caught by the exclusion lists, not here
])
def test_detect_product_tells_the_macs_apart(title, expected):
    assert detect_product(title) == expected


@pytest.mark.parametrize("series, expected", [
    ("Mac mini", "desktop"), ("Mac Studio", "desktop"),
    ("Air", "laptop"), ("Pro 13", "laptop"), ("Pro 14/16", "laptop"), ("Neo", "laptop"),
    (None, "laptop"), ("", "laptop"), (float("nan"), "laptop"),
])
def test_device_class_is_derived_from_the_series(series, expected):
    """Missing means laptop: every row written before desktops existed was one."""
    assert device_class(series) == expected


@pytest.mark.parametrize("series, key", [("Mac mini", "mini"), ("Mac Studio", "studio")])
@pytest.mark.parametrize("screen", [None, "", float("nan"), 13.3, 27.0])
def test_a_desktop_takes_its_own_form_factor_whatever_the_screen_says(series, key, screen):
    """The 13.3" fallback for a missing screen used to file every desktop under
    pro13; and a stray screen size read from the body must not move it either."""
    assert form_factor_key(series, screen) == key


def test_a_desktop_prints_no_screen_size():
    assert nominal_inches("Mac mini", None) is None
    assert nominal_inches("Mac Studio", 27) is None


def test_desktop_weights_are_read_from_the_same_place_as_laptop_weights():
    w = ScoringWeights(form_mini=1.3, form_studio=1.4)
    assert w.form_weight("mini") == 1.3
    assert w.form_weight("studio") == 1.4


def test_a_desktop_is_scored_by_both_entry_points_alike():
    row = {"chip": "M4", "ram_gb": 16, "ssd_gb": 256, "series": "Mac mini",
           "release_year": 2024, "price": 19900}
    weights = ScoringWeights()
    assert vfm_from_mapping(row, weights) == pytest.approx(
        get_vfm_score(MacBookSpec(**row), weights), abs=0.01)


def test_the_studio_only_chip_has_a_benchmark():
    """No laptop ships an M3 Ultra, so nothing needed it until desktops came in."""
    assert get_benchmark("M3 Ultra") > get_benchmark("M4 Max")


# ── Mixed listings: one title, several machines ───────────────────────────────
# A shop's "現貨" post covering several generations is priced from the cheapest.
# "mac mini m1 2012 2014/i7/16g/500g ssd+ 4tb" at NT$5,000 was scored as an M1
# from 2020 and topped the whole site at 1242. Nothing in it is one machine.

@pytest.mark.parametrize("title", [
    "真猛電腦 現貨mac mini m1 2012 2014/i7/16g/500g ssd+ 4tb",
    "Mac mini M1 / i7 任選",                       # i7 followed by a slash, not a space
    "MacBook Pro 13 M1 2019 2020 現貨",            # a pre-silicon model year in the title
    "MacBook Air 2017 M1 都有",
])
def test_a_title_mixing_apple_silicon_with_intel_era_signals_is_not_scored(title):
    assert force_extract_chip(title) is None


@pytest.mark.parametrize("title", [
    "MacBook Air M1 2020 8G/256G A2337",           # A2337 is a model number, not a year
    "MacBook Pro 14 M3 Pro 2023 保固至2025",
    "Mac mini M4 16G/256G 2024 全新",
    "MacBook Air M2 2022 電池循環 201 次",           # a three-digit count is not a year
])
def test_ordinary_apple_silicon_titles_still_extract(title):
    assert force_extract_chip(title) is not None


# ── The veto has to survive the LLM and the year rewrite ──────────────────────
# force_extract_chip() is only consulted when the LLM found no chip. When it
# found one -- in the very title that says "i7" -- the veto has to be asked
# again, and the parsed year is no help: infer_correct_year() rewrites a
# desktop's year from its chip, so the 2012 machine came back dated 2020.

def test_the_title_veto_is_asked_separately_from_the_chip():
    title = "真猛電腦 現貨mac mini m1 2012 2014/i7/16g/500g ssd+ 4tb"
    assert is_intel_era_title(title)
    # exactly what the pipeline stored for this row: chip and year both clean
    assert _is_intel_era_row("M1", 2020, title)


def test_a_variant_name_can_carry_the_signal_the_heading_hides():
    """Shopee titles are "<product> - <variant>". The shop names the machine in
    the option, not the heading, and L1 only ever saw the heading."""
    assert not is_intel_era_title("Apple MacBook Pro 二手筆電")
    assert is_intel_era_title("Apple MacBook Pro 二手筆電 - 2017 i5 8G/256G")


def test_a_pre_silicon_year_alone_still_vetoes():
    """No chip named, but the machine cannot be one this project scores."""
    assert is_intel_era_title("MacBook Pro 15 2015 16G/512G")


@pytest.mark.parametrize("title", [
    "MacBook Air M1 2020 8G/256G A2337",
    "Mac mini M4 16G/256G 2024 全新",
    "MacBook Pro 14 M3 Pro 2023 保固至2025",
])
def test_a_clean_title_is_not_vetoed(title):
    assert not is_intel_era_title(title)
    assert not _is_intel_era_row(force_extract_chip(title), 2024, title)


def test_a_row_with_no_chip_is_left_to_the_chip_filter():
    """Not this check's job: a chip-less row is discarded downstream, and
    saying yes here would blur the two reasons in the log."""
    assert not _is_intel_era_row(None, 2012, "MacBook Pro 2012 i7")


def test_an_unparseable_year_does_not_crash_the_veto():
    assert not _is_intel_era_row("M4", "未知", "Mac mini M4 16G/256G")
