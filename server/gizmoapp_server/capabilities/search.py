from __future__ import annotations

from typing import Any

from ..db import search_sample_nodes


def search_records(connection, query: str) -> dict[str, Any]:
    return {
        "query": query,
        "results": search_sample_nodes(connection, query),
        "source": "sqlite",
    }
