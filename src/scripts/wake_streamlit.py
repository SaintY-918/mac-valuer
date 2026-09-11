"""Wake a sleeping Streamlit Community Cloud app.

    python -m src.scripts.wake_streamlit

A plain HTTP GET does not count as visitor traffic, and proves nothing about
whether the app is actually running: Streamlit Cloud always serves 200 from
`mac-valuer.streamlit.app` -- what's behind it differs. The page that loads is
a shell that mounts the real app inside a same-origin iframe (observed at a
path like `/~/+/`) once its React bundle boots and opens a websocket; when the
app is asleep, that shell shows a "Yes, get this app back up!" button instead.
Neither the iframe's path nor which frame holds which state is documented, so
this searches every frame on the same host rather than hardcoding one.

Exit 0 means the app is reachable and awake (either it already was, or this
woke it); non-zero means it needs a human look -- a keep-alive job that can
fail quietly is worse than no keep-alive job at all.
"""

import os
import re
import sys
import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

APP_URL = os.getenv("STREAMLIT_APP_URL", "https://mac-valuer.streamlit.app")
PAGE_LOAD_TIMEOUT_MS = 30_000
WAKE_BUTTON_POLL_MS = 20_000
WAKE_TIMEOUT_MS = 90_000
POLL_INTERVAL_S = 1.0
APP_READY_SELECTOR = '[data-testid="stApp"]'

# Streamlit's own wording; kept loose (substring, case-insensitive) so a small
# copy change on their end doesn't silently turn this into a no-op.
WAKE_BUTTON_PATTERN = re.compile(r"get this app back up", re.IGNORECASE)


def _same_host_frames(page: Page, host: str):
    return [f for f in page.frames if urlparse(f.url).hostname == host]


def _find(page: Page, host: str, pattern_or_selector, by_text: bool):
    """First (frame, locator) on `host` whose target has at least one match."""
    for frame in _same_host_frames(page, host):
        try:
            locator = frame.get_by_text(pattern_or_selector) if by_text else frame.locator(pattern_or_selector)
            if locator.count() > 0:
                return frame, locator
        except PlaywrightError:
            continue
    return None, None


def _poll(page: Page, host: str, pattern_or_selector, by_text: bool, timeout_ms: int):
    """Poll _find across all matching frames until it hits or the deadline passes.

    A plain Playwright wait_for can't do this: the iframe holding either the
    wake button or the app itself may not exist in the DOM yet when this
    script starts polling, and wait_for only watches one already-known frame.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        frame, locator = _find(page, host, pattern_or_selector, by_text)
        if locator is not None:
            return frame, locator
        page.wait_for_timeout(int(POLL_INTERVAL_S * 1000))
    return None, None


def main() -> int:
    host = urlparse(APP_URL).hostname

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            page.goto(APP_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
        except PlaywrightError as e:
            print(f"::error::failed to load {APP_URL}: {e}")
            browser.close()
            return 1

        _frame, wake_button = _poll(page, host, WAKE_BUTTON_PATTERN, by_text=True, timeout_ms=WAKE_BUTTON_POLL_MS)

        if wake_button is None:
            # Not asleep is not the same as up -- confirm the app itself
            # rendered somewhere on this host rather than assuming it.
            _frame, app = _find(page, host, APP_READY_SELECTOR, by_text=False)
            if app is None:
                print(f"::error::{APP_URL} shows neither the app nor a wake button")
                browser.close()
                return 1
            print("app was already awake")
            browser.close()
            return 0

        print("app is asleep -- clicking the wake button")
        wake_button.first.click()

        _frame, app = _poll(page, host, APP_READY_SELECTOR, by_text=False, timeout_ms=WAKE_TIMEOUT_MS)
        if app is None:
            print("::error::clicked the wake button but the app never came up")
            browser.close()
            return 1

        print("app woke up")
        browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
