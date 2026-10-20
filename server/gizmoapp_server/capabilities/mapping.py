from __future__ import annotations

from typing import Any


DEFAULT_LOCATION = {
    "label": "UBC Vancouver",
    "latitude": 49.2606,
    "longitude": -123.2460,
    "zoom": 14,
    "source": "default",
}


def openstreetmap_config() -> dict[str, Any]:
    return {
        "provider": "openstreetmap",
        "tileUrlTemplate": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "Map data from OpenStreetMap contributors.",
        "defaultLocation": DEFAULT_LOCATION,
    }
