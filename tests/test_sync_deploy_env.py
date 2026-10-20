from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT_DIR / "scripts" / "sync_deploy_env.sh"


class SyncDeployEnvTestCase(unittest.TestCase):
    def test_sync_updates_allowed_git_tracked_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            scripts_dir = repo_root / "scripts"
            deploy_dir = repo_root / "deploy"
            scripts_dir.mkdir()
            deploy_dir.mkdir()

            (repo_root / ".env").write_text(
                "\n".join(
                    [
                        'GIZMOAPP_SHELL="text"',
                        'GIZMOAPP_URL_PREFIX="/demo-app"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (deploy_dir / "app.env").write_text(
                "\n".join(
                    [
                        "GIZMOAPP_SHELL=graphical",
                        "GIZMOAPP_URL_PREFIX=/should-be-ignored",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            envfile_source = ROOT_DIR / "scripts" / "envfile.py"
            (scripts_dir / "envfile.py").write_text(envfile_source.read_text(encoding="utf-8"), encoding="utf-8")
            (scripts_dir / "sync_deploy_env.sh").write_text(
                SYNC_SCRIPT.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (scripts_dir / "sync_deploy_env.sh").chmod(0o755)

            result = subprocess.run(
                ["bash", str(scripts_dir / "sync_deploy_env.sh")],
                cwd=repo_root,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 10)
            env_text = (repo_root / ".env").read_text(encoding="utf-8")
            self.assertIn('GIZMOAPP_SHELL="graphical"', env_text)
            self.assertIn('GIZMOAPP_URL_PREFIX="/demo-app"', env_text)
            self.assertIn("Ignoring unmanaged deploy setting GIZMOAPP_URL_PREFIX", result.stdout)


if __name__ == "__main__":
    unittest.main()
