from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.visual_verify import build_report_html, check_canvas_metrics, slugify, write_report


class VisualVerifyHelperTests(unittest.TestCase):
    def test_slugify_returns_safe_name(self) -> None:
        self.assertEqual(slugify("Desktop Large"), "desktop-large")
        self.assertEqual(slugify("***"), "viewport")

    def test_canvas_metrics_reject_blank_canvas(self) -> None:
        result = check_canvas_metrics(
            {
                "exists": True,
                "readable": True,
                "width": 800,
                "height": 600,
                "clientWidth": 800,
                "clientHeight": 600,
                "sampledPixels": 1000,
                "visiblePixels": 1000,
                "distinctColorBuckets": 1,
                "luminanceVariance": 0.1,
            }
        )

        self.assertFalse(result.ok)
        self.assertTrue(any("color variation" in failure for failure in result.failures))

    def test_canvas_metrics_accept_intentionally_blank_canvas(self) -> None:
        result = check_canvas_metrics(
            {
                "exists": True,
                "readable": True,
                "intentionalBlank": True,
                "width": 800,
                "height": 600,
                "clientWidth": 800,
                "clientHeight": 600,
                "sampledPixels": 1000,
                "visiblePixels": 1000,
                "distinctColorBuckets": 1,
                "luminanceVariance": 0.1,
            }
        )

        self.assertTrue(result.ok)
        self.assertTrue(any("intentionally blank" in warning for warning in result.warnings))

    def test_canvas_metrics_accept_rich_canvas(self) -> None:
        result = check_canvas_metrics(
            {
                "exists": True,
                "readable": True,
                "width": 1200,
                "height": 800,
                "clientWidth": 600,
                "clientHeight": 400,
                "sampledPixels": 1000,
                "visiblePixels": 1000,
                "distinctColorBuckets": 80,
                "luminanceVariance": 12,
            }
        )

        self.assertTrue(result.ok)

    def test_report_writer_creates_html_and_json(self) -> None:
        results = [
            {
                "viewport": {"name": "phone", "width": 390, "height": 844},
                "screenshot": "graphical-phone.png",
                "metrics": {"clientWidth": 390, "clientHeight": 700, "distinctColorBuckets": 20, "luminanceVariance": 8},
                "failures": [],
                "warnings": [],
                "ok": True,
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            write_report(output_dir, "http://127.0.0.1:8001/", results)

            self.assertTrue((output_dir / "index.html").exists())
            self.assertTrue((output_dir / "report.json").exists())
            self.assertIn("GizmoApp Visual Report", build_report_html(results, "http://127.0.0.1:8001/"))


if __name__ == "__main__":
    unittest.main()
