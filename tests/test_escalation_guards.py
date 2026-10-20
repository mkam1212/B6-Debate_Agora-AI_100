from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


class EscalationGuardTests(unittest.TestCase):
    def run_command(self, *command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=ROOT_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_network_install_targets_require_explicit_flag(self):
        result = self.run_command("make", "install-ml")

        self.assertEqual(result.returncode, 2)
        self.assertIn("ALLOW_NETWORK_INSTALL=1", result.stdout)

    def test_server_targets_require_explicit_flag(self):
        result = self.run_command("make", "dev")

        self.assertEqual(result.returncode, 2)
        self.assertIn("ALLOW_SERVER_RUN=1", result.stdout)

    def test_manage_run_dev_requires_explicit_flag(self):
        result = self.run_command(sys.executable, "server/manage.py", "run-dev")

        self.assertEqual(result.returncode, 2)
        self.assertIn("ALLOW_SERVER_RUN=1", result.stdout)

    def test_checkout_install_requires_explicit_flag(self):
        result = self.run_command("./scripts/install_checkout.sh")

        self.assertEqual(result.returncode, 2)
        self.assertIn("ALLOW_NETWORK_INSTALL=1", result.stdout)

    def test_deploy_script_requires_explicit_flag(self):
        result = self.run_command("./scripts/deploy_from_git.sh")

        self.assertEqual(result.returncode, 2)
        self.assertIn("ALLOW_DEPLOY_ACTIONS=1", result.stdout)

    def test_repo_deploy_wrapper_requires_explicit_flag_before_setup_checks(self):
        result = self.run_command("./scripts/deploy_gizmoapp_repo.sh", "git@github.com:example/demo.git")

        self.assertEqual(result.returncode, 2)
        self.assertIn("ALLOW_DEPLOY_ACTIONS=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
