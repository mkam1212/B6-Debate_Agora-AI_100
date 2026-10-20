#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def wait_until_ready(url: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | str | None = None
    while time.monotonic() < deadline:
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=min(2.0, timeout)) as response:
                payload = json.load(response)
                if response.status == 200 and payload.get("status") == "ready":
                    return payload
                last_error = f"HTTP {response.status}: {payload}"
        except (HTTPError, URLError, OSError, ValueError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for readiness at {url}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for a GizmoApp readiness endpoint.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    payload = wait_until_ready(args.url, args.timeout)
    print(f"Runtime is ready: {payload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
