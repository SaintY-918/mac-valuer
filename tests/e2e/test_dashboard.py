"""What the page actually shows, checked in a real browser.

Everything else in the suite tests functions. Nothing tested the page, and the
page is the product: a formula that scores correctly and then renders the
number into an element that overflows the viewport on a phone has still failed
the person using it.

Deliberately narrow. These do not test that the design is good — they test the
claims that can be wrong without anyone noticing:

  - the score on the card is the score the backend computes
  - a defect is flagged wherever it is described
  - the layout does not scroll sideways at any width worth supporting
  - the page raises no JavaScript errors
"""

import re

import pytest

from src.calculator.score_engine import ScoringWeights, vfm_from_mapping
from tests.e2e.conftest import DEFECT_URLS, LISTINGS

# Widths worth supporting: the narrowest phone still in use, the common modern
# phone, and a desktop window. 320 is the one that catches fixed-width mistakes.
VIEWPORTS = [(320, 680), (390, 844), (1440, 900)]


def _cards(page) -> list[dict]:
    """Read every rendered card back out of the DOM."""
    return page.eval_on_selector_all(".deal", """els => els.map(e => ({
        href: e.getAttribute('href'),
        title: e.querySelector('.deal__title')?.textContent ?? '',
        score: e.querySelector('.deal__box span')?.textContent ?? '',
        price: e.querySelector('.deal__price b')?.textContent ?? '',
        defect: e.querySelector('.badge--warn')?.getAttribute('title') ?? null,
    }))""")


def test_every_seeded_listing_is_rendered(page):
    assert {c["href"] for c in _cards(page)} == {row["url"] for row in LISTINGS}


def test_card_score_matches_the_backend(page):
    """The number on screen is the number score_engine computes, not a lookalike.

    This is the check the whole file exists for. The dashboard recomputes VFM
    live so the sliders can move, which is exactly the arrangement that let the
    page and the backend drift apart once already.
    """
    weights = ScoringWeights()
    expected = {
        row["url"]: f"{vfm_from_mapping(row['spec'], weights):.0f}"
        for row in LISTINGS
    }
    rendered = {c["href"]: c["score"] for c in _cards(page)}
    assert rendered == expected


def test_cards_are_ordered_by_score(page):
    scores = [float(c["score"]) for c in _cards(page)]
    assert scores == sorted(scores, reverse=True)


def test_prices_render_with_thousands_separators(page):
    by_url = {row["url"]: row["spec"]["price"] for row in LISTINGS}
    for card in _cards(page):
        assert card["price"] == f"{by_url[card['href']]:,}"


@pytest.mark.parametrize("row", [r for r in LISTINGS if r.get("defect")],
                         ids=lambda r: r["defect"])
def test_defects_are_flagged_wherever_they_are_described(page, row):
    """Title, parsed condition field, post body — all three must reach the badge.

    The body case is the one that was broken: find_defects accepts it, the
    dashboard passed it, and no read path ever returned it.

    A buyer who is not warned about a 瑕疵 loses more than one who is warned
    about a clean machine, so this errs toward flagging.
    """
    card = next(c for c in _cards(page) if c["href"] == row["url"])
    assert card["defect"], f"no defect badge on the listing whose defect is in the {row['defect']}"


def test_clean_listings_are_not_flagged(page):
    """A badge on every card is the same as a badge on none."""
    flagged = {c["href"] for c in _cards(page) if c["defect"]}
    assert flagged == DEFECT_URLS


def test_hiding_defects_removes_exactly_those_cards(fresh_page):
    before = _cards(fresh_page)
    fresh_page.get_by_test_id("stSidebar").get_by_text("隱藏瑕疵品").click()
    fresh_page.wait_for_function(
        f"document.querySelectorAll('.deal').length === {len(before) - len(DEFECT_URLS)}",
        timeout=30_000,
    )
    after = _cards(fresh_page)
    assert {c["href"] for c in after} == {c["href"] for c in before} - DEFECT_URLS
    assert not any(c["defect"] for c in after)


@pytest.mark.parametrize("width,height", VIEWPORTS, ids=lambda v: str(v))
def test_layout_never_scrolls_sideways(viewport, width, height):
    """A page that scrolls horizontally on a phone is unreadable, and nothing
    else in this project would notice it happening."""
    page = viewport(width, height)
    overflow = page.evaluate("""() => {
        const root = document.documentElement;
        if (root.scrollWidth <= window.innerWidth + 1) return null;
        // Name the widest offender rather than just reporting a number.
        let worst = null;
        for (const el of document.querySelectorAll('body *')) {
            const r = el.getBoundingClientRect();
            if (r.right > window.innerWidth + 1 && (!worst || r.right > worst.right)) {
                worst = {right: r.right, tag: el.tagName,
                         cls: (el.className || '').toString().slice(0, 60)};
            }
        }
        return {scrollWidth: root.scrollWidth, inner: window.innerWidth, worst};
    }""")
    assert overflow is None, f"horizontal overflow at {width}px: {overflow}"


@pytest.mark.parametrize("width,height", VIEWPORTS, ids=lambda v: str(v))
def test_card_text_stays_inside_its_card(viewport, width, height):
    """Overflow hidden by a clipping ancestor still loses the reader the text."""
    page = viewport(width, height)
    spills = page.eval_on_selector_all(".deal", """els => els.flatMap(card => {
        const outer = card.getBoundingClientRect();
        return [...card.querySelectorAll('.deal__title, .deal__price b, .deal__box span')]
            .filter(el => el.getBoundingClientRect().right > outer.right + 1)
            .map(el => el.className + ': ' + el.textContent.slice(0, 30));
    })""")
    assert spills == [], f"content spills out of its card at {width}px: {spills}"


def test_page_raises_no_javascript_errors(page):
    page.wait_for_timeout(1_000)
    assert page.errors == []


def test_heading_and_footer_are_present(page):
    assert page.locator("h1").first.is_visible()
    assert page.locator(".st-footer").count() == 1


def test_no_placeholder_or_error_text_leaked_into_the_page(page):
    """A traceback rendered as page text is still a page that "loaded"."""
    body = page.locator("body").inner_text()
    for smell in ("Traceback", "NameError", "KeyError", "st.error", "nan", "None GB"):
        assert smell not in body, f"page text contains {smell!r}"
    assert not re.search(r"\bNaN\b", body)
