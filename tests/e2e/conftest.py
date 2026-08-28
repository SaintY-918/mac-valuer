"""Fixtures for the browser tests: a seeded database and a live dashboard.

The suite proper is forbidden from touching a database. These tests need a
rendered page, so they build their own SQLite file in a temp directory and
point Streamlit at it — still no external service, still nothing to configure,
but now the numbers on screen have a known right answer.

Everything here is session-scoped: starting Streamlit costs about fifteen
seconds and the tests only read.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("playwright", reason="playwright is not installed")
pytest.importorskip("streamlit", reason="streamlit is not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

from src.parser.condition_flags import find_defects  # noqa: E402

# Fixed listings with fixed specs, so an expected score can be computed rather
# than read off the page it is meant to be checking. Every field the card
# renders is set explicitly; none of it comes from a live scrape.
#
# The defect cases are deliberately spread across all three places a defect can
# be described — the title, the parsed condition field, and the post body — and
# the clean listings are genuinely clean, so a false positive fails too.
LISTINGS = [
    {
        "url": "https://example.test/ptt/1",
        "source": "ptt",
        "title": "[販售] MacBook Pro 14 M3 Pro 18G/512G 2023 保內",
        "body": "盒裝配件齊全，電池循環 42 次。",
        "spec": {"price": 45000, "chip": "M3 Pro", "ram_gb": 18, "ssd_gb": 512,
                 "series": "Pro 14/16", "screen_size": 14, "release_year": 2023},
    },
    {
        "url": "https://example.test/ptt/2",
        "source": "ptt",
        "title": "[販售] MacBook Air 13 M1 8G/256G 2020",
        "body": "自用機，功能一切正常。",
        "spec": {"price": 18000, "chip": "M1", "ram_gb": 8, "ssd_gb": 256,
                 "series": "Air", "screen_size": 13, "release_year": 2020},
    },
    {
        "url": "https://example.test/shopee/3",
        "source": "shopee",
        "title": "MacBook Air 13 M2 16G/512G 螢幕有瑕疵 便宜賣",
        "body": "其餘功能正常。",
        "spec": {"price": 28000, "chip": "M2", "ram_gb": 16, "ssd_gb": 512,
                 "series": "Air", "screen_size": 13, "release_year": 2022},
        "defect": "title",
    },
    {
        "url": "https://example.test/carousell/4",
        "source": "carousell",
        "title": "MacBook Pro 16 M4 Max 48G/1TB 2024 近全新",
        "body": "購入三個月，一切正常。",
        "spec": {"price": 95000, "chip": "M4 Max", "ram_gb": 48, "ssd_gb": 1024,
                 "series": "Pro 14/16", "screen_size": 16, "release_year": 2024},
    },
    {
        "url": "https://example.test/ptt/5",
        "source": "ptt",
        "title": "[販售] MacBook Pro 14 M2 Pro 16G/512G 2023",
        "body": "機況說明：螢幕右下角破裂，不影響使用，介意者勿下標。",
        "spec": {"price": 38000, "chip": "M2 Pro", "ram_gb": 16, "ssd_gb": 512,
                 "series": "Pro 14/16", "screen_size": 14, "release_year": 2023},
        "defect": "body",
    },
    {
        "url": "https://example.test/shopee/6",
        "source": "shopee",
        "title": "MacBook Pro 14 M1 Pro 16G/512G 二手",
        "body": "詳情看商品頁。",
        "spec": {"price": 25000, "chip": "M1 Pro", "ram_gb": 16, "ssd_gb": 512,
                 "series": "Pro 14/16", "screen_size": 14, "release_year": 2021,
                 "condition": "C級 外觀有使用痕跡"},
        "defect": "condition",
    },
    {
        "url": "https://example.test/ptt/7",
        "source": "ptt",
        "title": "[販售] MacBook Air 13 M3 8G/256G 2024 全新未拆",
        "body": "禮物用不到，原封未拆。",
        "spec": {"price": 30000, "chip": "M3", "ram_gb": 8, "ssd_gb": 256,
                 "series": "Air", "screen_size": 13, "release_year": 2024},
    },
    {
        "url": "https://example.test/carousell/8",
        "source": "carousell",
        "title": "MacBook Air 15 M4 16G/512G 2024",
        "body": "公司採購多餘，全新未使用。",
        "spec": {"price": 40000, "chip": "M4", "ram_gb": 16, "ssd_gb": 512,
                 "series": "Air", "screen_size": 15, "release_year": 2024},
    },
    {
        # Written before defects were stored with the spec, so it carries no
        # defects key. The reader still has to be warned, from the title alone.
        "url": "https://example.test/ptt/9",
        "source": "ptt",
        "title": "[販售] MacBook Pro 13 M1 8G/256G 電池膨脹 零件機",
        "body": "無法開機。",
        "spec": {"price": 9000, "chip": "M1", "ram_gb": 8, "ssd_gb": 256,
                 "series": "Pro 13", "screen_size": 13, "release_year": 2020},
        "defect": "legacy row, no stored defects",
        "legacy": True,
    },
]

DEFECT_URLS = {row["url"] for row in LISTINGS if row.get("defect")}


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory) -> str:
    """Build the fixture database and return its SQLAlchemy URL."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.database.db_manager import Base, Deal

    path = tmp_path_factory.mktemp("e2e") / "fixture.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sessionmaker(bind=engine)() as session:
        for i, row in enumerate(LISTINGS):
            spec = dict(row["spec"])
            # Seeded the way the pipeline writes: defects are detected once,
            # while the body is still in hand, and stored with the spec. Rows
            # marked legacy skip that, so the fallback path gets exercised too.
            if not row.get("legacy"):
                spec["defects"] = find_defects(row["title"], spec.get("condition"), row["body"])
            session.add(Deal(
                url=row["url"],
                source=row["source"],
                title=row["title"],
                body_content=row["body"],
                parsed_json=json.dumps(spec, ensure_ascii=False),
                status="available",
                # Staggered so first_seen ordering is deterministic; well inside
                # the staleness window either way.
                first_seen=now - timedelta(hours=i),
                updated_at=now - timedelta(hours=i),
                last_seen=now - timedelta(hours=i),
            ))
        session.commit()
    engine.dispose()
    return f"sqlite:///{path.as_posix()}"


def _log(handle, path: Path) -> str:
    """Whatever Streamlit has written so far, for a failure message."""
    handle.flush()
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no log)"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def dashboard_url(seeded_db) -> str:
    """Run the real entrypoint against the fixture database."""
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": seeded_db,
        # The page must render without either of these; if it ever starts
        # needing them, that is the finding.
        "GEMINI_API_KEY": "",
        "DISCORD_WEBHOOK_URL": "",
        "PYTHONIOENCODING": "utf-8",
    }
    # Streamlit's output goes to a file, not a pipe. Nothing here drains a
    # pipe, so once the OS buffer filled the server would block mid-write and
    # stop serving — which is exactly what happened: the first few page loads
    # worked and every one after that timed out.
    log_path = Path(seeded_db.replace("sqlite:///", "")).parent / "streamlit.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "streamlit_app.py",
         "--server.port", str(port), "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail("streamlit exited early:\n" + _log(log, log_path))
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(0.5)
        else:
            pytest.fail("streamlit did not come up within 90s:\n" + _log(log, log_path))
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


DESKTOP = {"width": 1440, "height": 900}


def _open(browser, url):
    """Load the dashboard, collecting JS errors for the tests to assert on."""
    context = browser.new_context(viewport=dict(DESKTOP))
    pg = context.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
    pg.wait_for_selector(".deal", timeout=60_000)
    return context, pg


@pytest.fixture(scope="session")
def page(browser, dashboard_url):
    """One loaded page for every test that only reads it.

    Reloading per test cost ten seconds each and bought nothing: the data is
    fixed and read-only assertions cannot disturb one another. Tests that
    change widget state take `fresh_page` instead, and the viewport fixture
    puts the window back.
    """
    context, pg = _open(browser, dashboard_url)
    yield pg
    context.close()


@pytest.fixture
def fresh_page(browser, dashboard_url):
    """A page of its own, for tests that click something."""
    context, pg = _open(browser, dashboard_url)
    yield pg
    context.close()


@pytest.fixture
def viewport(page):
    """Resize the shared page, and put it back however the test ends."""
    def _set(width: int, height: int):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(400)
        return page
    yield _set
    page.set_viewport_size(dict(DESKTOP))
    page.wait_for_timeout(200)
