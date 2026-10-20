from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any


@dataclass(frozen=True)
class Capability:
    slug: str
    label: str
    description: str
    status: str
    endpoint: str | None = None
    optional_dependency: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "label": self.label,
            "description": self.description,
            "status": self.status,
            "endpoint": self.endpoint,
            "optionalDependency": self.optional_dependency,
        }


def optional_dependency_available(module_name: str) -> bool:
    return find_spec(module_name) is not None


def capability_payload(api_base: str, enabled_features: set[str] | frozenset[str]) -> dict[str, Any]:
    sklearn_available = optional_dependency_available("sklearn")
    def status_for(feature: str, available: bool = True) -> str:
        if feature not in enabled_features:
            return "disabled"
        return "ready" if available else "optional-dependency-missing"

    capabilities = [
        Capability(
            slug="audio",
            label="Audio",
            description="Analyze browser-captured sample arrays without extra server dependencies.",
            status=status_for("audio"),
            endpoint=f"{api_base}/audio/analyze",
        ),
        Capability(
            slug="search",
            label="Search",
            description="Search persisted records through the shared SQLite store.",
            status=status_for("search"),
            endpoint=f"{api_base}/search",
        ),
        Capability(
            slug="optimization",
            label="Optimization",
            description="Run small routing and ordering optimizations with pure Python defaults.",
            status=status_for("optimization"),
            endpoint=f"{api_base}/optimize/route",
        ),
        Capability(
            slug="mapping",
            label="Mapping",
            description="OpenStreetMap support is available when a derived app requests mapping.",
            status=status_for("mapping"),
            endpoint=f"{api_base}/map/default",
        ),
        Capability(
            slug="machine-learning",
            label="Machine Learning",
            description="Use scikit-learn lazily when a derived app requests ML behavior.",
            status=status_for("machine-learning", sklearn_available),
            endpoint=f"{api_base}/ml/kmeans",
            optional_dependency="scikit-learn",
        ),
        Capability(
            slug="rich-graphics",
            label="Rich Graphics",
            description="Canvas renderer supports layered sprites and bitmap textures by default.",
            status="ready",
        ),
    ]
    serialized = []
    for capability in capabilities:
        item = capability.as_dict()
        if capability.status == "disabled":
            item["endpoint"] = None
        serialized.append(item)
    return {"capabilities": serialized}
