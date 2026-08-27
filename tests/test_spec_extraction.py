"""RAM/SSD extraction from seller-written titles.

Every case here came from a real listing. The scraper feeds these strings
straight into the VFM formula, so a misread is worse than a miss: a missing
value gets filled by the LLM or falls back to a default, while a wrong one
produces a plausible-looking score nobody questions.
"""

import pytest

from src.parser.llm_parser import _VALID_RAM, _VALID_SSD, extract_specs_from_text


@pytest.mark.parametrize("title, expected", [
    # Formats sellers actually use
    ("16G/512G",                        (16, 512)),
    ("8G+512G",                         (8, 512)),
    ("16GB 256GB",                      (16, 256)),
    ("36G/2TB",                         (36, 2048)),
    ("32G/1TB",                         (32, 1024)),
    ("i5-1.4/8G/512GB",                 (8, 512)),
    ("MacBook Neo 8G/512G 13吋",         (8, 512)),
    # The unit may be followed by Chinese, so no word boundary after it
    ("M1 Pro 16G記憶體 512G SSD",         (16, 512)),
])
def test_reads_the_common_formats(title, expected):
    assert extract_specs_from_text(title) == expected


@pytest.mark.parametrize("title, expected", [
    # "8C10G" is 8 CPU cores and 10 GPU cores. Read as memory it gave RAM=10 and
    # an 8 TB SSD, and 8 TB cleared the >=1 TB threshold and inflated the score.
    ("『澄橘』Macbook Pro 13 2022 M2 8C10G/8G/256G 瑕疵機", (8, 256)),
    # The old scan stopped at the 7G/8G fragment, rejected RAM=7, and gave up
    # instead of continuing to the real pair behind it.
    ("『澄橘』Macbook Air 13 2020 M1 8C7G/8G/256G",         (8, 256)),
    ("MacBook Air 15吋 2026 M5 (10C/10G) 16G/512G",        (16, 512)),
])
def test_core_counts_are_not_memory(title, expected):
    assert extract_specs_from_text(title) == expected


@pytest.mark.parametrize("title", [
    "Apple MacBook Air 13吋 M1 2020 超值二手蘋果筆電 快速出貨",
    "【Apple】MacBook Pro 14吋 M3 Pro 晶片 A2992 [A級福利品]",
    "MacBook Pro 2019",
])
def test_returns_nothing_when_the_title_says_nothing(title):
    assert extract_specs_from_text(title) == (None, None)


def test_partial_reads_are_allowed():
    """Half an answer beats none — the other half still gets a fallback."""
    assert extract_specs_from_text("macbook Air 2020年 512G SSD") == (None, 512)


@pytest.mark.parametrize("title", [
    "MacBook 7G/9G",          # neither is a shipped size
    "MacBook Pro 8C10G",      # core counts only
])
def test_never_invents_a_configuration_apple_does_not_ship(title):
    ram, ssd = extract_specs_from_text(title)
    assert ram is None or ram in _VALID_RAM
    assert ssd is None or ssd in _VALID_SSD


def test_eight_alone_is_too_ambiguous_to_call():
    """8 is both a valid RAM size and a valid TB count. One bare 8 cannot be
    both, and guessing 8 TB would hand the listing an SSD bonus."""
    ram, ssd = extract_specs_from_text("MacBook Air 8G")
    assert ssd != 8192
