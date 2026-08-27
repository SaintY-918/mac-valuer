from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ModelSeries(str, Enum):
    AIR = "Air"
    PRO_13 = "Pro 13"
    PRO_14_16 = "Pro 14/16"

    # No weight property here. Scoring multipliers live in ScoringWeights
    # (src/calculator/score_engine.py) and nowhere else — this class once
    # carried a third, conflicting set of numbers that nothing consumed.

class MacBookSpec(BaseModel):
    chip: Optional[str] = Field(None, description="e.g., M1, M2 Pro, M3 Max")
    ram_gb: Optional[int] = Field(None, description="RAM size in GB")
    ssd_gb: Optional[int] = Field(None, description="SSD size in GB")
    screen_size: Optional[float] = Field(None, description="Screen size in inches")
    release_year: Optional[int] = Field(None, description="Year of model release")
    series: Optional[ModelSeries] = Field(None, description="Model series: Air, Pro 13, or Pro 14/16")
    price: Optional[float] = Field(None, description="Listing price of the device")
    location: Optional[str] = Field(None, description="Trading location, e.g., Taipei, Hsinchu")
    battery_health: Optional[int] = Field(None, description="Battery health percentage, e.g., 89")
    warranty_status: Optional[str] = Field(None, description="Warranty info, e.g., '2025-12' or '已過保'")
    condition: Optional[str] = Field(None, description="Physical condition, e.g., '全新', '輕微使用痕跡'")
    is_year_inferred: bool = False
    is_spec_inferred: bool = False
