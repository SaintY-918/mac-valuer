import datetime
from src.models.mac_spec import MacBookSpec
from src.utils.benchmark_db import get_benchmark

DEPRECIATION_RATE = 0.10  # 10% annual depreciation

def calculate_adjusted_score(spec: MacBookSpec) -> float:
    """
    Calculates the adjusted score based on base benchmark, depreciation, and weights.
    Formula: S_adj = BaseScore * 0.9^(CurrentYear - ReleaseYear) * RAMWeight * ModelWeight
    """
    # 1. Base Score
    base_score = get_benchmark(spec.chip)
    
    # 2. Year Depreciation (Compound Interest)
    current_year = datetime.datetime.now().year
    age = max(0, current_year - spec.release_year)
    depreciation_factor = (1 - DEPRECIATION_RATE) ** age
    adjusted_base = base_score * depreciation_factor
    
    # 3. Apply Weights
    final_score = adjusted_base * spec.ram_weight * spec.model_weight
    
    return final_score

def get_vfm_score(spec: MacBookSpec) -> float:
    """
    Calculates the Value-for-Money (VFM) score.
    Formula: VFM = (AdjustedScore / Price) * 1000
    """
    if spec.price <= 0:
        return 0.0
    
    adjusted_score = calculate_adjusted_score(spec)
    vfm_score = (adjusted_score / spec.price) * 1000
    return vfm_score
