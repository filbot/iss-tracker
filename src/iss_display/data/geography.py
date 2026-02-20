"""Simple geography library to map coordinates to common area designations."""

from __future__ import annotations

from typing import NamedTuple, List, Optional


class Region(NamedTuple):
    name: str
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


# Coarse bounding boxes for major land masses.
# Order matters: checked sequentially.
LAND_REGIONS: List[Region] = [
    Region("Antarctica", -90, -75, -180, 180),
    Region("Australia", -50, -10, 110, 180),
    Region("South America", -60, 15, -85, -35),
    Region("North America", 15, 85, -170, -50),
    Region("Africa", -35, 38, -20, 55),
    Region("Europe", 35, 72, -25, 60),
    Region("Asia", 5, 80, 60, 180),
    # Catch-all for SE Asia islands if not caught by Asia/Australia
    Region("Asia", -10, 30, 90, 160), 
]


def get_common_area_name(lat: float, lon: float) -> str:
    """
    Returns a common area designation (Continent or Ocean) for the given coordinates.
    """
    
    # 1. Check Land Regions
    for region in LAND_REGIONS:
        if (region.min_lat <= lat <= region.max_lat) and (region.min_lon <= lon <= region.max_lon):
            return region.name

    # 2. Fallback to Oceans
    if lat > 65:
        return "Arctic"
    if lat < -60:
        return "Southern"

    if -80 <= lon <= 20:
        return "Atlantic"

    if 20 < lon <= 100:
        return "Indian"

    # Pacific is the rest (roughly 100 to 180 and -180 to -80)
    return "Pacific"
