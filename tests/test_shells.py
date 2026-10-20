from __future__ import annotations

import unittest

from server.gizmoapp_server.shells import classify_shell_paths, resolve_shell_variant


class ShellSelectionTests(unittest.TestCase):
    def test_auto_prefers_text_for_text_shell_changes(self) -> None:
        self.assertEqual(
            classify_shell_paths({"server/gizmoapp_server/static/app/text/main.js"}),
            "text",
        )

    def test_auto_prefers_graphical_for_graphical_shell_changes(self) -> None:
        self.assertEqual(
            classify_shell_paths({"server/gizmoapp_server/static/app/scene.js"}),
            "graphical",
        )

    def test_auto_falls_back_when_changes_are_mixed_or_unknown(self) -> None:
        self.assertIsNone(
            classify_shell_paths(
                {
                    "server/gizmoapp_server/static/app/scene.js",
                    "server/gizmoapp_server/static/app/text/main.js",
                }
            )
        )
        self.assertIsNone(classify_shell_paths({"server/gizmoapp_server/db.py"}))

    def test_resolver_supports_auto_and_explicit_shells(self) -> None:
        self.assertEqual(resolve_shell_variant("text"), "text")
        self.assertEqual(resolve_shell_variant("graphical"), "graphical")
        self.assertEqual(resolve_shell_variant("auto"), "graphical")

    def test_resolver_rejects_unknown_shells(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown shell"):
            resolve_shell_variant("typo")


if __name__ == "__main__":
    unittest.main()
