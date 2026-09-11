"""api/main.py had zero coverage — every endpoint, `_attach_vfm`, and
`_compute_thresholds` were untested even though the dashboard's `_recalc_vfm`
must stay in sync with `_attach_vfm` per .spec/specs/api/spec.md.

Uses the same throwaway-SQLite-per-test fixture as tests/test_retirement.py:
DBManager() reads DATABASE_URL fresh on every construction, and api/main.py
constructs a new DBManager() per request rather than taking one as a
dependency, so pointing the env var at a tmp file before the request is
enough — no FastAPI dependency override needed.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.database.db_manager import DBManager, Deal


@pytest.fixture
def client():
    tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db".replace("\\", "/")
    from api.main import app
    return TestClient(app)


def _seed(url, price, chip="M3", ram_gb=16, ssd_gb=512, status="available"):
    db = DBManager()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    parsed = {
        "chip": chip, "ram_gb": ram_gb, "ssd_gb": ssd_gb, "screen_size": 14.0,
        "release_year": 2023, "series": "Pro 14/16", "price": price, "location": "台北",
    }
    with db.Session() as s:
        s.add(Deal(url=url, source="ptt", status=status, title=url,
                    body_content="", parsed_json=json.dumps(parsed, ensure_ascii=False),
                    first_seen=now, updated_at=now, last_seen=now))
        s.commit()


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_deals_empty_db_returns_default_thresholds(client):
    resp = client.get("/api/deals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["deals"] == []
    assert body["vfm_thresholds"] == {"p50": 250.0, "p75": 350.0}


def test_deals_lists_seeded_available_deal_with_vfm_score(client):
    _seed("https://example.com/1", price=30000)
    resp = client.get("/api/deals")
    body = resp.json()
    assert body["count"] == 1
    deal = body["deals"][0]
    assert deal["vfm_score"] is not None
    assert deal["vfm_score"] > 0


def test_deals_status_filter_excludes_sold_by_default(client):
    _seed("https://example.com/sold", price=20000, status="sold")
    resp = client.get("/api/deals")
    assert resp.json()["count"] == 0

    resp = client.get("/api/deals", params={"status": "sold"})
    assert resp.json()["count"] == 1


def test_deals_price_filter(client):
    _seed("https://example.com/cheap", price=10000)
    _seed("https://example.com/pricey", price=90000)
    resp = client.get("/api/deals", params={"min_price": 50000})
    assert resp.json()["count"] == 1
    assert resp.json()["deals"][0]["price"] == 90000


def test_deals_sorted_by_vfm_score_descending(client):
    # Same chip/ram/ssd, different price — lower price is a better deal (higher VFM).
    _seed("https://example.com/a", price=50000)
    _seed("https://example.com/b", price=20000)
    resp = client.get("/api/deals")
    deals = resp.json()["deals"]
    assert deals[0]["price"] == 20000
    assert deals[0]["vfm_score"] >= deals[1]["vfm_score"]


def test_deals_custom_weights_change_score(client):
    _seed("https://example.com/1", price=30000, ram_gb=32)
    baseline = client.get("/api/deals").json()["deals"][0]["vfm_score"]
    boosted = client.get("/api/deals", params={"ram_multiplier": 5.0}).json()["deals"][0]["vfm_score"]
    assert boosted > baseline


def test_attach_vfm_handles_corrupt_series_without_raising(client):
    # parsed_json is stored as raw JSON, not re-validated on read — a `series`
    # value outside the ModelSeries enum (e.g. written by an older schema)
    # must degrade to vfm_score=None, not take down the whole /api/deals response.
    _seed("https://example.com/corrupt", price=30000, chip="M3")
    db = DBManager()
    with db.Session() as s:
        deal = s.get(Deal, "https://example.com/corrupt")
        parsed = json.loads(deal.parsed_json)
        parsed["series"] = "Not A Real Series"
        deal.parsed_json = json.dumps(parsed, ensure_ascii=False)
        s.commit()

    resp = client.get("/api/deals")
    assert resp.status_code == 200
    assert resp.json()["deals"][0]["vfm_score"] is None


def test_score_calculate_endpoint(client):
    resp = client.post("/api/score/calculate", json={
        "spec": {"chip": "M3", "ram_gb": 16, "ssd_gb": 512, "screen_size": 14.0,
                  "release_year": 2023, "series": "Pro 14/16", "price": 30000},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["vfm_score"] > 0


def test_score_calculate_rejects_invalid_series(client):
    resp = client.post("/api/score/calculate", json={
        "spec": {"chip": "M3", "price": 30000, "series": "Not A Real Series"},
    })
    assert resp.status_code == 422
