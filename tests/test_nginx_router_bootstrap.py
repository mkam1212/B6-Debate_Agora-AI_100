from __future__ import annotations

import unittest

from scripts.nginx_router_bootstrap import ensure_managed_include


class NginxRouterBootstrapTestCase(unittest.TestCase):
    def test_inserts_include_into_single_server_block(self):
        original = "server {\n    listen 80;\n}\n"

        updated, changed = ensure_managed_include(
            original,
            "/etc/nginx/gizmoapp-instances/*.conf",
        )

        self.assertTrue(changed)
        self.assertIn("include /etc/nginx/gizmoapp-instances/*.conf;", updated)
        self.assertLess(updated.index("listen 80;"), updated.index("include /etc/nginx/gizmoapp-instances/*.conf;"))

    def test_uses_matching_server_name_when_multiple_blocks_exist(self):
        original = (
            "server {\n"
            "    listen 80;\n"
            "    server_name example.test;\n"
            "}\n"
            "server {\n"
            "    listen 80;\n"
            "    server_name vickrey10.cs.ubc.ca;\n"
            "}\n"
        )

        updated, changed = ensure_managed_include(
            original,
            "/etc/nginx/gizmoapp-instances/*.conf",
            server_name="vickrey10.cs.ubc.ca",
        )

        self.assertTrue(changed)
        self.assertEqual(updated.count("include /etc/nginx/gizmoapp-instances/*.conf;"), 1)
        target_block = (
            "server {\n"
            "    listen 80;\n"
            "    server_name vickrey10.cs.ubc.ca;\n"
            "    include /etc/nginx/gizmoapp-instances/*.conf;\n"
            "}\n"
        )
        self.assertIn(target_block, updated)


if __name__ == "__main__":
    unittest.main()
