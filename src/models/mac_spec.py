from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# Configurations Apple actually ships. Kept here rather than inside the parser
# because they are facts about the hardware, and both the parser and the
# dashboard's filters need them — the filter lists had drifted, stopping at
# 64 GB and 2 TB while the parser already accepted 128 GB and 8 TB.
VALID_RAM_GB = (8, 16, 18, 24, 32, 36, 48, 64, 96, 128)
VALID_SSD_GB = (128, 256, 512, 1024, 2048, 4096, 8192)


class ModelSeries(str, Enum):
    AIR = "Air"
    PRO_13 = "Pro 13"
    PRO_14_16 = "Pro 14/16"
    # The MacBook Neo (March 2026) is neither an Air nor a Pro. Without a value
    # here the parser cannot describe one at all: pydantic rejects anything
    # outside the enum, so a Neo came back with no series and fell through the
    # form-factor default.
    NEO = "Neo"

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
