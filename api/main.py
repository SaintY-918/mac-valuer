import logging
from typing import Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel

from src.calculator.score_engine import ScoringWeights, get_vfm_score
from src.database.db_manager import DBManager
from src.models.mac_spec import MacBookSpec

logger = logging.getLogger(__name__)

app = FastAPI(
    title="mac-valuer API",
    description="二手 MacBook 行情查詢與 VFM 評分引擎",
    version="0.1.0",
)


class ScoreRequest(BaseModel):
    spec: MacBookSpec
    weights: ScoringWeights = ScoringWeights()


class ScoreResponse(BaseModel):
    vfm_score: float


def _attach_vfm(deal: dict, weights: ScoringWeights) -> dict:
    try:
        spec = MacBookSpec(
            chip=deal.get("chip"),
            ram_gb=deal.get("ram_gb"),
            ssd_gb=deal.get("ssd_gb"),
            screen_size=deal.get("screen_size"),
            release_year=deal.get("release_year"),
            series=deal.get("series"),
            price=deal.get("price"),
            location=deal.get("location"),
        )
        deal["vfm_score"] = round(get_vfm_score(spec, weights), 2)
    except Exception as e:
        logger.warning("VFM score failed for %s: %s", deal.get("url", "?"), e)
        deal["vfm_score"] = None
    return deal


@app.get("/api/deals")
def list_deals(
    status: str = Query("available", description="'available' or 'sold'"),
    min_price: Optional[float] = Query(None, description="最低價格（新台幣）"),
    max_price: Optional[float] = Query(None, description="最高價格（新台幣）"),
    ram_gb: Optional[int] = Query(None, description="RAM 大小（GB），例如 16"),
    chip: Optional[str] = Query(None, description="晶片型號（模糊比對），例如 M3"),
    source: Optional[str] = Query(None, description="來源平台，例如 ptt"),
):
    """列出符合條件的二手 Mac 物件，每筆附帶 VFM 分數，預設只回傳 available 狀態。"""
    db = DBManager()
    deals = db.get_filtered_deals(
        status=status,
        min_price=min_price,
        max_price=max_price,
        ram_gb=ram_gb,
        chip=chip,
        source=source,
    )
    weights = ScoringWeights()
    deals = [_attach_vfm(d, weights) for d in deals]
    deals.sort(key=lambda d: d.get("vfm_score") or 0, reverse=True)
    return {"count": len(deals), "deals": deals}


@app.post("/api/score/calculate", response_model=ScoreResponse)
def calculate_score(body: ScoreRequest):
    """傳入規格與自訂權重，回傳 VFM 分數。"""
    score = get_vfm_score(body.spec, body.weights)
    return ScoreResponse(vfm_score=round(score, 4))


@app.get("/api/health")
def health():
    return {"status": "ok"}
