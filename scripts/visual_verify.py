#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT_DIR / "var" / "visual-report"
DEFAULT_VIEWPORTS = (
    {"name": "phone", "width": 390, "height": 844, "device_scale_factor": 2},
    {"name": "tablet", "width": 834, "height": 1112, "device_scale_factor": 2},
    {"name": "desktop", "width": 1440, "height": 960, "device_scale_factor": 1},
)

CANVAS_METRICS_JS = """
() => {
  const canvas = document.querySelector("#scene-canvas");
  if (!canvas) {
    return { exists: false };
  }

  const rect = canvas.getBoundingClientRect();
  const base = {
    exists: true,
    readable: true,
    intentionalBlank: canvas.dataset.intentionalBlank === "true",
    clientWidth: rect.width,
    clientHeight: rect.height,
    width: canvas.width,
    height: canvas.height,
  };

  try {
    const context = canvas.getContext("2d");
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const pixelCount = Math.max(1, data.length / 4);
    const stride = Math.max(4, Math.floor(pixelCount / 30000) * 4);
    const colors = new Set();
    let sampledPixels = 0;
    let visiblePixels = 0;
    let luminanceSum = 0;
    let luminanceSquareSum = 0;

    for (let index = 0; index < data.length; index += stride) {
      const red = data[index];
      const green = data[index + 1];
      const blue = data[index + 2];
      const alpha = data[index + 3];
      if (alpha > 10) {
        visiblePixels += 1;
      }
      const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
      luminanceSum += luminance;
      luminanceSquareSum += luminance * luminance;
      sampledPixels += 1;
      colors.add(`${red >> 4}-${green >> 4}-${blue >> 4}-${alpha >> 4}`);
    }

    const mean = luminanceSum / sampledPixels;
    const variance = Math.max(0, luminanceSquareSum / sampledPixels - mean * mean);
    return {
      ...base,
      sampledPixels,
      visiblePixels,
      distinctColorBuckets: colors.size,
      luminanceMean: mean,
      luminanceVariance: variance,
    };
  } catch (error) {
    return {
      ...base,
      readable: false,
      error: String(error),
    };
  }
}
"""


@dataclass(frozen=True)
class CheckResult:
    failures: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.failures


def slugify(value: str) -> str:
    cleaned = [character.lower() if character.isalnum() else "-" for character in value]
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "viewport"


def check_canvas_metrics(metrics: dict[str, Any]) -> CheckResult:
    failures: list[str] = []
    warnings: list[str] = []

    if not metrics.get("exists"):
        return CheckResult(["#scene-canvas was not found"], warnings)

    width = float(metrics.get("width") or 0)
    height = float(metrics.get("height") or 0)
    client_width = float(metrics.get("clientWidth") or 0)
    client_height = float(metrics.get("clientHeight") or 0)

    if width < 240 or height < 240:
        failures.append(f"canvas backing store is too small ({width:g}x{height:g})")
    if client_width < 240 or client_height < 240:
        failures.append(f"canvas display size is too small ({client_width:g}x{client_height:g})")

    if not metrics.get("readable", True):
        warnings.append(f"canvas pixels could not be read: {metrics.get('error', 'unknown error')}")
        return CheckResult(failures, warnings)

    if metrics.get("intentionalBlank"):
        warnings.append("canvas is marked as an intentionally blank starter surface")
        return CheckResult(failures, warnings)

    sampled_pixels = int(metrics.get("sampledPixels") or 0)
    visible_pixels = int(metrics.get("visiblePixels") or 0)
    distinct_color_buckets = int(metrics.get("distinctColorBuckets") or 0)
    luminance_variance = float(metrics.get("luminanceVariance") or 0)

    if sampled_pixels < 100:
        failures.append("not enough canvas pixels were sampled")
    if sampled_pixels and visible_pixels / sampled_pixels < 0.95:
        failures.append("canvas appears mostly transparent")
    if distinct_color_buckets < 8:
        failures.append("canvas has too little color variation and may be blank")
    if luminance_variance < 2:
        failures.append("canvas has too little luminance variation and may be flat")

    return CheckResult(failures, warnings)


def find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_for_url(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, URLError) as error:
            last_error = error
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def wait_for_server(process: subprocess.Popen, url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = ""
            if process.stdout is not None:
                output = process.stdout.read()
            raise RuntimeError(f"Server exited before {url} was reachable.\n{output.strip()}")
        try:
            with urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, URLError) as error:
            last_error = error
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def start_server(host: str, port: int, shell: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["GIZMOAPP_SHELL"] = shell
    env["ALLOW_SERVER_RUN"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [
            sys.executable,
            "server/manage.py",
            "run-dev",
            "--host",
            host,
            "--port",
            str(port),
            "--shell",
            shell,
        ],
        cwd=ROOT_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process


def stop_server(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def capture_visuals(base_url: str, output_dir: Path, viewports: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    sync_playwright = load_playwright()
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for viewport in viewports:
                name = slugify(str(viewport["name"]))
                page = browser.new_page(
                    viewport={"width": int(viewport["width"]), "height": int(viewport["height"])},
                    device_scale_factor=float(viewport.get("device_scale_factor", 1)),
                )
                screenshot_path = output_dir / f"graphical-{name}.png"
                browser_errors: list[str] = []
                page.on("pageerror", lambda error: browser_errors.append(str(error)))
                page.on(
                    "console",
                    lambda message: browser_errors.append(message.text) if message.type == "error" else None,
                )
                try:
                    page.goto(base_url, wait_until="networkidle", timeout=15000)
                    page.wait_for_selector("#scene-canvas", timeout=5000)
                    page.wait_for_function(
                        "() => document.querySelector('#scene-canvas')?.dataset.rendered === 'true'",
                        timeout=5000,
                    )
                    page.wait_for_timeout(250)
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    metrics = page.evaluate(CANVAS_METRICS_JS)
                    checks = check_canvas_metrics(metrics)
                    failures = [*checks.failures]
                    if browser_errors:
                        failures.extend(f"browser error: {error}" for error in browser_errors)
                    results.append(
                        {
                            "viewport": viewport,
                            "screenshot": screenshot_path.name,
                            "metrics": metrics,
                            "failures": failures,
                            "warnings": checks.warnings,
                            "ok": not failures,
                        }
                    )
                finally:
                    page.close()
        finally:
            browser.close()

    return results


def load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Playwright is required for visual verification. "
            "Manual setup may need network access: run ALLOW_NETWORK_INSTALL=1 make visual-install "
            "from a user-approved shell."
        ) from error
    return sync_playwright


def build_report_html(results: list[dict[str, Any]], base_url: str) -> str:
    cards = []
    for result in results:
        viewport = result["viewport"]
        failures = result.get("failures", [])
        warnings = result.get("warnings", [])
        metrics = result.get("metrics", {})
        status = "ok" if result.get("ok") else "needs work"
        messages = failures or warnings or ["Canvas pixel checks passed. Inspect the screenshot before ending the turn."]
        message_items = "\n".join(f"<li>{html.escape(str(message))}</li>" for message in messages)
        cards.append(
            f"""
            <section class="card">
              <h2>{html.escape(str(viewport["name"]))} <span>{html.escape(status)}</span></h2>
              <img src="{html.escape(result["screenshot"])}" alt="{html.escape(str(viewport["name"]))} screenshot">
              <dl>
                <dt>Viewport</dt><dd>{viewport["width"]} x {viewport["height"]}</dd>
                <dt>Canvas</dt><dd>{metrics.get("clientWidth", 0):.0f} x {metrics.get("clientHeight", 0):.0f}</dd>
                <dt>Color buckets</dt><dd>{metrics.get("distinctColorBuckets", "n/a")}</dd>
                <dt>Luminance variance</dt><dd>{metrics.get("luminanceVariance", "n/a")}</dd>
              </dl>
              <ul>{message_items}</ul>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>GizmoApp Visual Report</title>
    <style>
      body {{
        margin: 0;
        padding: 24px;
        background: #f6f5ef;
        color: #18201c;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      header {{ max-width: 1120px; margin: 0 auto 20px; }}
      h1 {{ margin: 0 0 8px; font-size: 28px; }}
      p {{ margin: 0; color: #626b64; }}
      main {{ max-width: 1120px; margin: 0 auto; display: grid; gap: 18px; }}
      .card {{ border: 1px solid rgba(39, 49, 43, 0.14); border-radius: 8px; background: white; padding: 16px; }}
      .card h2 {{ display: flex; justify-content: space-between; gap: 12px; margin: 0 0 12px; font-size: 18px; }}
      .card h2 span {{ color: #12766f; font-size: 14px; }}
      img {{ display: block; width: 100%; height: auto; border: 1px solid rgba(39, 49, 43, 0.14); border-radius: 8px; }}
      dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 6px 12px; margin: 14px 0; }}
      dt {{ color: #626b64; }}
      dd {{ margin: 0; font-weight: 650; }}
      ul {{ margin: 0; padding-left: 20px; }}
    </style>
  </head>
  <body>
    <header>
      <h1>GizmoApp Visual Report</h1>
      <p>Rendered from {html.escape(base_url)}. Inspect these screenshots before declaring graphics work finished.</p>
    </header>
    <main>
      {''.join(cards)}
    </main>
  </body>
</html>
"""


def write_report(output_dir: Path, base_url: str, results: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (output_dir / "index.html").write_text(build_report_html(results, base_url), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture and validate GizmoApp graphical shell screenshots.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=0, type=int, help="Use 0 to choose a free local port.")
    parser.add_argument("--base-url", help="Use an already running app instead of constructing one from host/port.")
    parser.add_argument("--no-start-server", action="store_true", help="Do not start the Flask development server.")
    parser.add_argument("--shell", default="graphical", choices=["graphical", "auto"])
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    process: subprocess.Popen | None = None

    try:
        load_playwright()
        port = args.port or find_free_port(args.host)
        base_url = args.base_url or f"http://{args.host}:{port}/"
        if not args.no_start_server:
            process = start_server(args.host, port, args.shell)
            wait_for_server(process, f"http://{args.host}:{port}/healthz")
        else:
            wait_for_url(base_url)

        results = capture_visuals(base_url, output_dir, DEFAULT_VIEWPORTS)
        write_report(output_dir, base_url, results)
        failed = [result for result in results if not result["ok"]]
        print(f"Visual report: {output_dir / 'index.html'}")
        print(f"JSON report: {output_dir / 'report.json'}")
        if failed:
            for result in failed:
                print(f"{result['viewport']['name']}: " + "; ".join(result["failures"]), file=sys.stderr)
            return 1
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        stop_server(process)


if __name__ == "__main__":
    raise SystemExit(main())
