from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.gizmoapp_server.config import load_feature_intent, load_settings, load_shell_intent, normalize_url_prefix


class ConfigTestCase(unittest.TestCase):
    def test_dotenv_overrides_tracked_deploy_defaults_without_mutating_process_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "deploy").mkdir()
            (repo_root / "deploy" / "app.env").write_text("GIZMOAPP_SHELL=graphical\n", encoding="utf-8")
            (repo_root / ".env").write_text("GIZMOAPP_SHELL=text\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GIZMOAPP_SHELL", None)
                settings = load_settings(repo_root=repo_root)
                self.assertNotIn("GIZMOAPP_SHELL", os.environ)

            self.assertEqual(settings["APP_SHELL"], "text")

    def test_load_settings_reads_git_tracked_deploy_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            deploy_dir = repo_root / "deploy"
            deploy_dir.mkdir()
            (deploy_dir / "app.env").write_text(
                "GIZMOAPP_SHELL=text\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GIZMOAPP_SHELL", None)
                settings = load_settings(repo_root=repo_root)

            self.assertEqual(settings["APP_SHELL"], "text")

    def test_load_settings_reads_shell_intent_when_requested_shell_is_auto(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            deploy_dir = repo_root / "deploy"
            deploy_dir.mkdir()
            (deploy_dir / "app.env").write_text(
                "GIZMOAPP_SHELL=auto\n",
                encoding="utf-8",
            )
            (deploy_dir / "app-shell.txt").write_text(
                "# Public shell intent\ntext\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GIZMOAPP_SHELL", None)
                settings = load_settings(repo_root=repo_root)

            self.assertEqual(load_shell_intent(repo_root), "text")
            self.assertEqual(settings["APP_SHELL"], "text")
            self.assertEqual(load_settings(shell_variant="auto", repo_root=repo_root)["APP_SHELL"], "text")

    def test_explicit_shell_env_overrides_shell_intent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            deploy_dir = repo_root / "deploy"
            deploy_dir.mkdir()
            (deploy_dir / "app-shell.txt").write_text("text\n", encoding="utf-8")

            with patch.dict(os.environ, {"GIZMOAPP_SHELL": "graphical"}, clear=False):
                settings = load_settings(repo_root=repo_root)

            self.assertEqual(settings["APP_SHELL"], "graphical")

    def test_load_settings_reads_repo_root_dotenv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / ".env").write_text(
                "\n".join(
                    [
                        'GIZMOAPP_APP_NAME="Derived Demo"',
                        "GIZMOAPP_URL_PREFIX=/demo-app",
                        "GIZMOAPP_SHELL=text",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GIZMOAPP_APP_NAME", None)
                os.environ.pop("GIZMOAPP_URL_PREFIX", None)
                os.environ.pop("GIZMOAPP_SHELL", None)
                settings = load_settings(repo_root=repo_root)

            self.assertEqual(settings["APP_NAME"], "Derived Demo")
            self.assertEqual(settings["URL_PREFIX"], "/demo-app")
            self.assertEqual(settings["APP_SHELL"], "text")

    def test_process_env_overrides_repo_root_dotenv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            deploy_dir = repo_root / "deploy"
            deploy_dir.mkdir()
            (deploy_dir / "app.env").write_text(
                "GIZMOAPP_SHELL=text\n",
                encoding="utf-8",
            )
            (repo_root / ".env").write_text(
                "GIZMOAPP_URL_PREFIX=/from-dotenv\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"GIZMOAPP_URL_PREFIX": "/from-environ", "GIZMOAPP_SHELL": "graphical"},
                clear=False,
            ):
                settings = load_settings(repo_root=repo_root)

            self.assertEqual(settings["URL_PREFIX"], "/from-environ")
            self.assertEqual(settings["APP_SHELL"], "graphical")

    def test_relative_database_path_is_resolved_from_repo_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / ".env").write_text("GIZMOAPP_DB_PATH=custom/data.sqlite3\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GIZMOAPP_DB_PATH", None)
                settings = load_settings(repo_root=repo_root)
            self.assertEqual(settings["DB_PATH"], repo_root / "custom" / "data.sqlite3")

    def test_sqlite_profile_defaults_to_efs_safe_values_and_is_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GIZMOAPP_SQLITE_JOURNAL_MODE", None)
                os.environ.pop("GIZMOAPP_SQLITE_SYNCHRONOUS", None)
                settings = load_settings(repo_root=repo_root)
            self.assertEqual(settings["SQLITE_JOURNAL_MODE"], "DELETE")
            self.assertEqual(settings["SQLITE_SYNCHRONOUS"], "FULL")

            with patch.dict(os.environ, {"GIZMOAPP_SQLITE_JOURNAL_MODE": "unsafe"}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "SQLITE_JOURNAL_MODE"):
                    load_settings(repo_root=repo_root)

    def test_invalid_prefix_and_shell_intent_fail_with_actionable_errors(self):
        with self.assertRaises(RuntimeError):
            normalize_url_prefix("/demo app")
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "deploy").mkdir()
            (repo_root / "deploy" / "app-shell.txt").write_text("typo\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Invalid shell intent"):
                load_shell_intent(repo_root)

    def test_feature_intent_is_opt_in_and_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "deploy").mkdir()
            (repo_root / "deploy" / "features.txt").write_text(
                "audio, optimization # requested features\n",
                encoding="utf-8",
            )
            self.assertEqual(load_feature_intent(repo_root), frozenset({"audio", "optimization"}))
            (repo_root / "deploy" / "features.txt").write_text("unknown\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Invalid feature intent"):
                load_feature_intent(repo_root)

    def test_production_rejects_default_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            with patch.dict(
                os.environ,
                {"GIZMOAPP_ENV": "production", "GIZMOAPP_SECRET_KEY": "dev-only-secret"},
                clear=False,
            ):
                with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
                    load_settings(repo_root=repo_root)


if __name__ == "__main__":
    unittest.main()
