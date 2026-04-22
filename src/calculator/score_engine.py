import datetime
from pydantic import BaseModel

from src.models.mac_spec import MacBookSpec
from src.utils.benchmark_db import get_benchmark

DEPRECIATION_RATE = 0.10


class ScoringWeights(BaseModel):
    ram_multiplier: float = 1.25    # applied when ram_gb >= 16
    ssd_multiplier: float = 1.1     # applied when ssd_gb >= 1024 (1 TB+)
    model_weight_air: float = 1.0
    model_weight_pro13: float = 1.0
    model_weight_pro14_16: float = 1.25


def _model_weight(spec: MacBookSpec, weights: ScoringWeights) -> float:
    series = str(spec.series or "").lower()
    if "14" in series or "16" in series:
        return weights.model_weight_pro14_16
    if "pro" in series:
        return weights.model_weight_pro13
    return weights.model_weight_air


def calculate_adjusted_score(spec: MacBookSpec, weights: ScoringWeights) -> float:
    base_score = get_benchmark(spec.chip)
    age = max(0, datetime.datetime.now().year - (spec.release_year or 2020))
    depreciation = (1 - DEPRECIATION_RATE) ** age

    ram_mult = weights.ram_multiplier if (spec.ram_gb or 0) >= 16 else 1.0
    ssd_mult = weights.ssd_multiplier if (spec.ssd_gb or 0) >= 1024 else 1.0
    model_mult = _model_weight(spec, weights)

    return base_score * depreciation * ram_mult * ssd_mult * model_mult


def get_vfm_score(
    spec: MacBookSpec,
    weights: ScoringWeights = ScoringWeights(),
) -> float:
    if not spec.price or spec.price <= 0:
        return 0.0
    return (calculate_adjusted_score(spec, weights) / spec.price) * 1000
