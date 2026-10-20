from __future__ import annotations

import math
from typing import Any

MAX_ROUTE_POINTS = 500


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def nearest_neighbor_route(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    raw_points = payload.get("points", [])
    errors: list[str] = []

    if not isinstance(raw_points, list) or len(raw_points) < 2:
        return {}, ["points must contain at least two coordinate objects"]
    if len(raw_points) > MAX_ROUTE_POINTS:
        return {}, [f"points must contain at most {MAX_ROUTE_POINTS} coordinate objects"]

    points: list[dict[str, Any]] = []
    for index, raw_point in enumerate(raw_points):
        try:
            x = float(raw_point["x"])
            y = float(raw_point["y"])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("coordinates must be finite")
            point_id = raw_point.get("id", str(index))
            if not isinstance(point_id, (str, int, float)) or len(str(point_id)) > 100:
                raise ValueError("invalid point id")
            points.append(
                {
                    "id": point_id,
                    "x": x,
                    "y": y,
                }
            )
        except (TypeError, ValueError, KeyError, AttributeError):
            errors.append("each point must include numeric x and y values")
            break

    if errors:
        return {}, errors

    remaining = points[1:]
    route = [points[0]]
    total_distance = 0.0

    while remaining:
        current = route[-1]
        next_point = min(remaining, key=lambda point: _distance(current, point))
        total_distance += _distance(current, next_point)
        route.append(next_point)
        remaining.remove(next_point)

    return {
        "route": route,
        "orderedIds": [point["id"] for point in route],
        "distance": total_distance,
        "method": "nearest-neighbor",
    }, []
