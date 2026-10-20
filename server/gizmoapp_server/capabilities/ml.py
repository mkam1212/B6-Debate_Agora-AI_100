from __future__ import annotations

from importlib.util import find_spec
import math
from typing import Any

MAX_KMEANS_POINTS = 10_000


def sklearn_status() -> dict[str, Any]:
    available = find_spec("sklearn") is not None
    return {
        "available": available,
        "package": "scikit-learn",
        "installCommand": "ALLOW_NETWORK_INSTALL=1 make install-ml",
    }


def run_kmeans(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], int]:
    raw_points = payload.get("points", [])
    errors: list[str] = []

    try:
        cluster_count = int(payload.get("clusters", 2))
    except (TypeError, ValueError, OverflowError):
        return {}, ["clusters must be an integer between 1 and 8"], 400

    if not isinstance(raw_points, list):
        return {}, ["points must be a list of two-number coordinate arrays"], 400
    if len(raw_points) > MAX_KMEANS_POINTS:
        errors.append(f"points must contain at most {MAX_KMEANS_POINTS} coordinate arrays")
    if len(raw_points) < cluster_count:
        errors.append("points must contain at least as many points as clusters")

    if cluster_count < 1 or cluster_count > 8:
        errors.append("clusters must be between 1 and 8")

    try:
        points = [[float(point[0]), float(point[1])] for point in raw_points]
        if any(not math.isfinite(value) for point in points for value in point):
            raise ValueError("coordinates must be finite")
    except (TypeError, ValueError, IndexError, OverflowError):
        errors.append("points must be two-number coordinate arrays")
        points = []

    if errors:
        return {}, errors, 400

    status = sklearn_status()
    if not status["available"]:
        return {"status": status}, ["scikit-learn is not installed"], 503

    from sklearn.cluster import KMeans

    model = KMeans(n_clusters=cluster_count, n_init="auto", random_state=0)
    labels = model.fit_predict(points)
    return {
        "labels": [int(label) for label in labels],
        "centers": model.cluster_centers_.tolist(),
        "inertia": float(model.inertia_),
        "status": status,
    }, [], 200
