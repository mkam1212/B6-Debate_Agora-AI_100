from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from server.gizmoapp_server import create_app
from server.gizmoapp_server.db import LATEST_SCHEMA_VERSION


ALL_FEATURES = frozenset(
    {"admin", "audio", "machine-learning", "mapping", "optimization", "sample-nodes", "search"}
)


class GizmoAppTestCase(unittest.TestCase):
    def make_app(
        self,
        url_prefix: str = "",
        shell_variant: str = "graphical",
        enabled_features: frozenset[str] = ALL_FEATURES,
        **overrides,
    ):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.sqlite3"
        app = create_app(
            {
                "TESTING": True,
                "DB_PATH": db_path,
                "URL_PREFIX": url_prefix,
                "SECRET_KEY": "test-secret",
                "AUTO_MIGRATE": True,
                "ENABLED_FEATURES": enabled_features,
                **overrides,
            },
            shell_variant=shell_variant,
        )
        return app

    def tearDown(self):
        temp_dir = getattr(self, "temp_dir", None)
        if temp_dir is not None:
            temp_dir.cleanup()

    def test_bootstrap_and_readiness_use_prefix(self):
        app = self.make_app("/demo-app")
        client = app.test_client()

        bootstrap = client.get("/demo-app/api/bootstrap")
        ready = client.get("/demo-app/readyz")

        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.get_json()["app"]["shell"], "graphical")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.get_json()["status"], "ready")
        self.assertEqual(ready.get_json()["schemaVersion"], LATEST_SCHEMA_VERSION)

    def test_shared_preview_uses_each_members_trusted_mount(self):
        app = self.make_app(TRUST_PROXY=True)
        for prefix in ("/user/alice/cw/group-preview/team", "/user/bob/cw/group-preview/team", ""):
            page = app.test_client().get("/", headers={"X-Forwarded-Prefix": prefix}).get_data(as_text=True)
            self.assertIn(f'<base href="{prefix}/">', page)
            self.assertIn(f'{prefix}/app/', page)
            self.assertIn(f'{prefix}/api', page)
        self.assertEqual(app.config["URL_PREFIX"], "")

    def test_shared_preview_header_cannot_override_untrusted_or_fixed_prefix(self):
        app = self.make_app(TRUST_PROXY=False)
        page = app.test_client().get("/", headers={"X-Forwarded-Prefix":"/untrusted"}).get_data(as_text=True)
        self.assertNotIn('/untrusted', page)
        app.config["URL_PREFIX"] = "/fixed"
        page = app.test_client().get("/", headers={"X-Forwarded-Prefix":"/other"}).get_data(as_text=True)
        self.assertIn('<base href="/fixed/">', page)

    def test_optional_routes_are_disabled_by_default(self):
        app = self.make_app(enabled_features=frozenset())
        client = app.test_client()

        self.assertEqual(client.get("/admin/").status_code, 404)
        response = client.post("/api/audio/analyze", json={"samples": [0], "sampleRate": 1})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content_type, "application/json")
        statuses = {
            item["slug"]: item["status"]
            for item in client.get("/api/capabilities").get_json()["capabilities"]
        }
        self.assertEqual(statuses["audio"], "disabled")
        self.assertEqual(statuses["mapping"], "disabled")

    def test_pwa_routes_are_not_exposed(self):
        app = self.make_app("/demo-app")
        client = app.test_client()

        self.assertEqual(client.get("/demo-app/manifest.webmanifest").status_code, 404)
        self.assertEqual(client.get("/demo-app/sw.js").status_code, 404)

    def test_can_insert_and_search_sample_node(self):
        app = self.make_app()
        client = app.test_client()
        response = client.post(
            "/api/sample-nodes",
            json={
                "slug": "compass",
                "label": "Compass",
                "description": "Created by the test suite.",
                "accent_color": "#72d1c2",
                "x": 0.6,
                "y": 0.4,
                "radius": 0.12,
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["sampleNode"]["slug"], "compass")
        search = client.get("/api/search?q=compass")
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.get_json()["results"][0]["slug"], "compass")

    def test_json_endpoints_reject_non_object_json(self):
        app = self.make_app()
        client = app.test_client()

        for path in ("/api/sample-nodes", "/api/audio/analyze", "/api/optimize/route"):
            with self.subTest(path=path):
                response = client.post(path, json=[1])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.content_type, "application/json")
                self.assertIn("must be an object", response.get_json()["errors"][0])

    def test_json_endpoints_require_json_content_type(self):
        app = self.make_app()
        response = app.test_client().post("/api/audio/analyze", data="samples=1")

        self.assertEqual(response.status_code, 415)
        self.assertIn("application/json", response.get_json()["errors"][0])

    def test_non_finite_and_wrong_type_values_are_rejected(self):
        app = self.make_app()
        client = app.test_client()

        route = client.post(
            "/api/optimize/route",
            json={"points": [{"id": "a", "x": "nan", "y": 0}, {"id": "b", "x": 1, "y": 1}]},
        )
        audio = client.post("/api/audio/analyze", json={"samples": ["nan"], "sampleRate": 1})
        sample = client.post("/api/sample-nodes", json={"slug": "obj-label", "label": {"bad": True}})

        self.assertEqual(route.status_code, 400)
        self.assertEqual(audio.status_code, 400)
        self.assertEqual(sample.status_code, 400)
        self.assertNotIn("NaN", route.get_data(as_text=True))

    def test_payload_size_limit_returns_json(self):
        app = self.make_app(MAX_CONTENT_LENGTH=16_384)
        response = app.test_client().post(
            "/api/audio/analyze",
            data='{"samples":["' + ("1" * 20_000) + '"]}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.content_type, "application/json")

    def test_capability_endpoints_validate_and_respond(self):
        app = self.make_app()
        client = app.test_client()

        audio = client.post("/api/audio/analyze", json={"samples": [0, 0.5, -0.5, 0], "sampleRate": 4})
        route = client.post(
            "/api/optimize/route",
            json={"points": [{"id": "a", "x": 0, "y": 0}, {"id": "b", "x": 1, "y": 0}]},
        )
        mapping = client.get("/api/map/default")
        ml_status = client.get("/api/ml/status")
        invalid_ml = client.post("/api/ml/kmeans", json={"clusters": "nope", "points": [[0, 0], [1, 1]]})

        self.assertEqual(audio.status_code, 200)
        self.assertAlmostEqual(audio.get_json()["durationSeconds"], 1.0)
        self.assertEqual(route.get_json()["orderedIds"], ["a", "b"])
        self.assertEqual(mapping.get_json()["defaultLocation"]["label"], "UBC Vancouver")
        self.assertIn("available", ml_status.get_json())
        self.assertEqual(invalid_ml.status_code, 400)

    def test_response_hardening_and_request_id(self):
        app = self.make_app()
        response = app.test_client().get("/api/bootstrap")

        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertRegex(response.headers["X-Request-ID"], r"^[0-9a-f]{16}$")

    def test_graphical_and_text_shells_include_error_boundary(self):
        for shell in ("graphical", "text"):
            with self.subTest(shell=shell):
                app = self.make_app(shell_variant=shell)
                html = app.test_client().get("/").get_data(as_text=True)
                self.assertIn('id="app-error"', html)
                self.assertIn("boot.js", html)
                self.assertNotIn("manifest.webmanifest", html)
                self.tearDown()

    def test_error_boundary_ignores_benign_resize_observer_notifications(self):
        boot_source = (
            Path(__file__).parents[1]
            / "server"
            / "gizmoapp_server"
            / "static"
            / "app"
            / "boot.js"
        ).read_text(encoding="utf-8")

        self.assertIn("ResizeObserver loop limit exceeded", boot_source)
        self.assertIn("ResizeObserver loop completed with undelivered notifications.", boot_source)
        self.assertIn("event.preventDefault()", boot_source)

    def test_admin_is_available_only_when_enabled(self):
        app = self.make_app(enabled_features=frozenset({"admin"}))
        response = app.test_client().get("/admin/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Deployment-facing details", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
