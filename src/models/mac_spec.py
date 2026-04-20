from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional

class ModelSeries(str, Enum):
    AIR = "Air"
    PRO_13 = "Pro 13"
    PRO_14_16 = "Pro 14/16"

    @property
    def weight(self) -> float:
        weights = {
            ModelSeries.AIR: 1.0,
            ModelSeries.PRO_13: 1.05,
            ModelSeries.PRO_14_16: 1.25
        }
        return weights[self]

class MacBookSpec(BaseModel):
    chip: Optional[str] = Field(None, description="e.g., M1, M2 Pro, M3 Max")
    ram_gb: Optional[int] = Field(None, description="RAM size in GB")
    ssd_gb: Optional[int] = Field(None, description="SSD size in GB")
    screen_size: Optional[float] = Field(None, description="Screen size in inches")
    release_year: Optional[int] = Field(None, description="Year of model release")
    series: Optional[ModelSeries] = Field(None, description="Model series: Air, Pro 13, or Pro 14/16")
    price: Optional[float] = Field(None, description="Listing price of the device")
    location: Optional[str] = Field(None, description="Trading location, e.g., Taipei, Hsinchu")
    is_year_inferred: bool = False
    is_spec_inferred: bool = False
    
    @property
    def ram_weight(self) -> float:
        return 1.2 if (self.ram_gb and self.ram_gb >= 16) else 1.0

    @property
    def model_weight(self) -> float:
        return self.series.weight if self.series else 1.0
