from __future__ import annotations

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SCENE_JS = ROOT_DIR / "server" / "gizmoapp_server" / "static" / "app" / "scene.js"
STYLES_CSS = ROOT_DIR / "server" / "gizmoapp_server" / "static" / "app" / "styles.css"


class GraphicsDefaultsTests(unittest.TestCase):
    def test_scene_renderer_defaults_to_bitmap_sprites(self) -> None:
        source = SCENE_JS.read_text(encoding="utf-8")

        self.assertIn(
            "function createSpriteTexture",
            source,
            "Keep createSpriteTexture as the test-backed sprite/bitmap helper name unless the contract changes.",
        )
        self.assertIn("this.nodeSprites", source)
        self.assertIn("this.context.drawImage", source)
        self.assertIn("createNodeSprite", source)

    def test_blank_scene_does_not_start_a_permanent_animation_loop(self) -> None:
        source = SCENE_JS.read_text(encoding="utf-8")
        constructor = source[source.index("constructor(canvas)"):source.index("resize()")]
        self.assertNotIn("requestAnimationFrame", constructor)
        self.assertIn("needsAnimation()", source)
        self.assertIn("document.hidden", source)

    def test_canvas_backing_dimensions_cannot_change_layout_height(self) -> None:
        source = STYLES_CSS.read_text(encoding="utf-8")
        workspace_rules = source[source.index(".graphics-workspace {"):source.index("#scene-canvas {")]
        canvas_rules = source[source.index("#scene-canvas {"):source.index(".admin-body {")]

        self.assertIn("min-height: 28rem", workspace_rules)
        self.assertIn("position: absolute", canvas_rules)
        self.assertIn("inset: 0", canvas_rules)
        self.assertIn("min-height: 0", canvas_rules)


if __name__ == "__main__":
    unittest.main()
