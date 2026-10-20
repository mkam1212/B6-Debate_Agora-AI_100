from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class AgentScaffoldingTests(unittest.TestCase):
    def run_command(self, *command: str, cwd: Path = ROOT_DIR) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_js_checker_runs_without_node(self):
        result = self.run_command(sys.executable, "scripts/check_js_syntax.py")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Node not required", result.stdout)

    def test_always_loaded_agent_context_stays_focused(self):
        agent_words = (ROOT_DIR / "AGENTS.md").read_text(encoding="utf-8").split()
        map_words = (ROOT_DIR / "docs" / "agent-map.md").read_text(encoding="utf-8").split()

        # AGENTS.md is loaded on every agent turn, so this ceiling is a ratchet
        # against regrowth, not an arbitrary number. Raise it only for a rule
        # that must be in the always-loaded file, and put the detail in
        # README.md instead wherever that will do.
        self.assertLessEqual(len(agent_words), 750)
        self.assertLessEqual(len(map_words), 350)

    def test_checkout_install_uses_image_pip_without_upgrading_it(self):
        installer = (ROOT_DIR / "scripts" / "install_checkout.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('dependency_python="${ROOT_DIR}/.venv/bin/python"', installer)
        self.assertIn('"${dependency_python}" -m pip install -r', installer)
        self.assertNotIn("pip\" install --upgrade pip wheel", installer)

    def test_commit_ready_sets_local_identity_and_removes_stale_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            init = self.run_command("git", "init", cwd=repo)
            self.assertEqual(init.returncode, 0, init.stdout)

            lock_path = repo / ".git" / "index.lock"
            lock_path.write_text("", encoding="utf-8")
            old_timestamp = time.time() - 3600
            os.utime(lock_path, (old_timestamp, old_timestamp))

            result = self.run_command(
                sys.executable,
                str(ROOT_DIR / "scripts" / "prepare_git_commit.py"),
                "--repo",
                str(repo),
                "--fix",
                "--stale-seconds",
                "1",
            )

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("Set repo-local user.name", result.stdout)
            self.assertIn("Set repo-local user.email", result.stdout)
            self.assertIn("Removed stale Git index lock", result.stdout)
            self.assertFalse(lock_path.exists())

            name = self.run_command("git", "config", "--local", "--get", "user.name", cwd=repo)
            email = self.run_command("git", "config", "--local", "--get", "user.email", cwd=repo)
            self.assertEqual(name.stdout.strip(), "Codex")
            self.assertEqual(email.stdout.strip(), "codex@local")


if __name__ == "__main__":
    unittest.main()
