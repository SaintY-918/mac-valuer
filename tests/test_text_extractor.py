"""PTT structured-section extraction.

PTT posts follow a [TAG] convention, so these fields can be read by rule rather
than handed to the LLM. That only helps if the rules actually fire.
"""

import pytest

from src.parser.text_extractor import (
    extract_location,
    extract_price,
    extract_spec_line,
    extract_warranty,
)


@pytest.mark.parametrize("body, expected", [
    # "25k" is how PTT sellers usually write it. The pattern carried a literal
    # backspace byte instead of \b, so it could never match and every k-priced
    # listing fell through with no price.
    ("[售價] 25k",        25000.0),
    ("[售價] 32K 可議",    32000.0),
    ("[售價] 1.5k",       1500.0),
    ("[售價] 25000",      25000.0),
    ("[售價] 28,000",     28000.0),
])
def test_reads_prices_written_either_way(body, expected):
    assert extract_price(body) == expected


@pytest.mark.parametrize("body", [
    "[售價] 沒寫",
    "[售價] 500",        # too low to be a machine; likely a typo or an accessory
    "[標題] 沒有售價區塊",
])
def test_returns_nothing_rather_than_a_wrong_number(body):
    assert extract_price(body) is None


def test_handles_full_width_brackets():
    """PTT posts mix ASCII [] and full-width ［］ freely."""
    assert extract_price("［售價］30000") == 30000.0


def test_reads_the_first_line_of_a_section_only():
    body = "[交易方式/地點] 台北面交\n其他細節不算在內"
    assert extract_location(body) == "台北面交"


def test_stops_at_the_next_tag():
    body = "[售價] 30000\n[保固] 已過保"
    assert extract_price(body) == 30000.0
    assert extract_warranty(body) == "已過保"


def test_missing_sections_are_absent_not_empty():
    assert extract_location("[售價] 30000") is None
    assert extract_warranty("[售價] 30000") is None
    assert extract_spec_line("[售價] 30000") is None


def test_no_tracked_file_carries_a_stray_control_character():
    """The price bug was invisible on screen: a raw 0x08 byte sitting inside a
    raw string where \\b was meant. Nothing but a scan will catch the next one.

    Every tracked text file, not only Python. The scan started at *.py and
    promptly missed the same byte landing twice in score-engine/spec.md and
    twice in CHANGELOG.md, from the same cause — writing \\b through a shell
    heredoc. A spec that silently says "do not use ``" teaches the next reader
    nothing.
    """
    import subprocess

    patterns = ["*.py", "*.md", "*.toml", "*.yml", "*.yaml", "*.ps1", "*.json", "*.ini"]
    tracked = subprocess.check_output(["git", "ls-files", *patterns], text=True).split()
    offenders = []
    for path in tracked:
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            bad = [ord(c) for c in line if ord(c) < 32 and c != "\t"]
            if bad:
                offenders.append(f"{path}:{lineno} {bad}")
    assert not offenders, "control characters found: " + "; ".join(offenders)
