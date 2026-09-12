import math
from enum import Enum
from typing import Any, Optional

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
    # Desktops. A Mac mini and a Mac Studio are the same kind of thing — a box
    # with no screen and no battery — and differ from each other the way an Air
    # differs from a Pro, so they sit at this level rather than in a class of
    # their own. iMac (has a screen) and Mac Pro (almost no second-hand market,
    # and "Mac Pro" collides with "MacBook Pro") are deliberately absent; see
    # docs/decisions.md.
    MAC_MINI = "Mac mini"
    MAC_STUDIO = "Mac Studio"

    # No weight property here. Scoring multipliers live in ScoringWeights
    # (src/calculator/score_engine.py) and nowhere else — this class once
    # carried a third, conflicting set of numbers that nothing consumed.


# The screenless ones. Everything that has to know whether a listing is a
# laptop — the scorer's form factor, the parser's required fields, the
# dashboard's mode switch — derives it from here rather than keeping a list.
DESKTOP_SERIES = frozenset({ModelSeries.MAC_MINI.value, ModelSeries.MAC_STUDIO.value})

DEVICE_CLASSES = ("laptop", "desktop")
DEVICE_CLASS_LABELS = {"laptop": "筆電", "desktop": "桌機"}


def device_class(series: Any) -> str:
    """"laptop" or "desktop", from a series value in whatever form it arrives.

    Not stored in parsed_json: it is a function of `series`, and a second copy
    is a second thing to drift. A missing series (None, NaN, "") is a laptop —
    every row written before desktops existed has no series worth the name,
    and they were all laptops.
    """
    if series is None:
        return "laptop"
    if isinstance(series, ModelSeries):
        name = series.value
    else:
        if isinstance(series, float) and math.isnan(series):
            return "laptop"
        name = str(series)
    return "desktop" if name in DESKTOP_SERIES else "laptop"


class MacBookSpec(BaseModel):
    """One parsed listing. The name predates desktops; it covers every Mac."""

    chip: Optional[str] = Field(None, description="e.g., M1, M2 Pro, M3 Max")
    ram_gb: Optional[int] = Field(None, description="RAM size in GB")
    ssd_gb: Optional[int] = Field(None, description="SSD size in GB")
    screen_size: Optional[float] = Field(None, description="Screen size in inches")
    release_year: Optional[int] = Field(None, description="Year of model release")
    series: Optional[ModelSeries] = Field(
        None, description="Model series: Air, Pro 13, Pro 14/16, Neo, Mac mini, Mac Studio")
    price: Optional[float] = Field(None, description="Listing price of the device")
    location: Optional[str] = Field(None, description="Trading location, e.g., Taipei, Hsinchu")
    battery_health: Optional[int] = Field(None, description="Battery health percentage, e.g., 89")
    warranty_status: Optional[str] = Field(None, description="Warranty info, e.g., '2025-12' or '已過保'")
    condition: Optional[str] = Field(None, description="Physical condition, e.g., '全新', '輕微使用痕跡'")
    is_year_inferred: bool = False
    is_spec_inferred: bool = False
