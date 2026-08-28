"""Defect detection.

The VFM formula rewards a low price, and a broken machine is cheap because it is
broken — so defective units float to the top. On live data the two highest
scores were a 瑕疵機 and an 外接機 (sold as a desktop because the screen is
dead), the first carrying the "最划算" badge.

Precision matters more than recall here. A wrong badge on a good machine
teaches the reader to ignore the badge, which costs more than a missed one.
"""

import pytest

from src.parser.condition_flags import defects_for, find_defects, has_defect


@pytest.mark.parametrize("text", [
    "『澄橘』Macbook Air 15 2025 M4 午夜 瑕疵機《二手》",
    "MacBook Air M1 A2337｜8G / 256G｜外接機 含原廠充電器",
    "C 級 | 上蓋無法密合,8C8G/8GB/256G",
    "C 級 | 螢幕右下邊框破裂 邊緣刻傷",
    "二手筆電 電池膨脹 需更換",
])
def test_flags_faults_that_stop_the_machine_working(text):
    assert has_defect(text), f"missed a real defect in: {text}"


@pytest.mark.parametrize("text", [
    # Contains the words a naive scan looks for, and means the opposite
    "外觀無傷無碰撞",
    "完全無撞無傷",
])
def test_negation_inverts_the_match(text):
    assert find_defects(text) == []


@pytest.mark.parametrize("text", [
    "A級，輕微刮傷或痕跡",
    "B級: 良好  一至兩處可視刮傷/ 撞傷",
    "明顯使用痕跡或凹痕",
])
def test_cosmetic_wear_is_not_a_defect(text):
    """Every used machine has marks. Flagging them would make the badge noise."""
    assert find_defects(text) == []


@pytest.mark.parametrize("text", [
    "MacBook Air M4 16G/256G 天藍 全新未拆",
    "九成新 店保七天",
    "[販售] 台北 macbook air M4 16G/256G",
])
def test_clean_listings_stay_clean(text):
    assert find_defects(text) == []


def test_searches_across_every_field_it_is_given():
    assert has_defect("MacBook Pro 14", "螢幕破裂", None)


@pytest.mark.parametrize("junk", [float("nan"), None, 123, "   ", object()])
def test_tolerates_whatever_the_caller_passes(junk):
    """Callers hand over raw DataFrame cells, where a missing value is NaN — a
    float, and truthy, so `if value` does not filter it. This crashed the page."""
    assert find_defects("好機器", junk) == []
    assert find_defects("瑕疵機", junk) == ["瑕疵"]


def test_reports_which_terms_matched():
    """The badge shows these on hover, so the reader can judge the call."""
    found = find_defects("C 級 | 螢幕右下邊框破裂")
    assert "破裂" in found and "C級" in found


# ── defects_for: which of the two paths a row takes ───────────────────────────
# The body is only available while the pipeline is running, so detection happens
# there and the result is stored. Everything downstream reads the stored list.

def test_defects_for_prefers_the_list_stored_at_parse_time():
    """The stored list is authoritative: it is the only one that saw the body."""
    row = {"original_title": "看起來很乾淨的標題", "defects": ["破裂"]}
    assert defects_for(row) == ["破裂"]


def test_defects_for_trusts_a_stored_empty_list():
    """An empty list means 'checked, nothing found' — not 'go and look again'."""
    row = {"original_title": "MacBook Air M2 瑕疵", "defects": []}
    assert defects_for(row) == []


def test_defects_for_falls_back_for_rows_parsed_before_the_change():
    """Rows already in the database have no defects key and must still warn."""
    row = {"original_title": "[販售] MacBook Pro 13 M1 電池膨脹 零件機"}
    assert defects_for(row)


def test_defects_for_accepts_either_title_key():
    """get_filtered_deals renames title to original_title; both reach here."""
    assert defects_for({"title": "螢幕破裂"}) == defects_for({"original_title": "螢幕破裂"})
