from __future__ import annotations

import json
import os
import runpy
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.check_runtime_ready import wait_until_ready


ROOT_DIR = Path(__file__).resolve().parents[1]


class _ReadyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = json.dumps({"status": "ready", "database": "ready"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        return


class DeploymentHardeningTests(unittest.TestCase):
    def test_gunicorn_defaults_to_one_sqlite_writer(self):
        config = (ROOT_DIR / "deploy" / "gunicorn.conf.py").read_text(encoding="utf-8")
        self.assertIn('workers = env_int("GIZMOAPP_GUNICORN_WORKERS", 1, 1, 16)', config)

    def test_shell_scripts_parse(self):
        scripts = [
            "scripts/deploy_from_git.sh",
            "scripts/install_deployment_instance.sh",
            "scripts/run_deploy_cron.sh",
        ]
        result = subprocess.run(
            ["bash", "-n", *scripts],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_deploy_script_has_lock_marker_validation_backup_rollback_and_readiness(self):
        source = (ROOT_DIR / "scripts" / "deploy_from_git.sh").read_text(encoding="utf-8")
        for expected in (
            "flock -n",
            "deployed-commit",
            "run_local_validation.sh",
            "backup-db",
            "rollback_deploy",
            "git reset --hard",
            "check_runtime_ready.py",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)

    def test_installer_refuses_dirty_or_ahead_checkouts_and_checks_readiness(self):
        source = (ROOT_DIR / "scripts" / "install_deployment_instance.sh").read_text(encoding="utf-8")
        self.assertIn("status --porcelain", source)
        self.assertIn("local commits not present", source)
        self.assertIn("flock 8", source)
        self.assertIn("check_runtime_ready.py", source)

    def test_runtime_readiness_waiter_accepts_ready_json(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ReadyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = wait_until_ready(f"http://127.0.0.1:{server.server_port}/readyz", 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(payload["status"], "ready")

    def test_gunicorn_config_rejects_invalid_numeric_environment(self):
        config_path = ROOT_DIR / "deploy" / "gunicorn.conf.py"
        previous = os.environ.get("GIZMOAPP_GUNICORN_WORKERS")
        os.environ["GIZMOAPP_GUNICORN_WORKERS"] = "many"
        try:
            with self.assertRaisesRegex(RuntimeError, "GIZMOAPP_GUNICORN_WORKERS must be an integer"):
                runpy.run_path(str(config_path))
        finally:
            if previous is None:
                os.environ.pop("GIZMOAPP_GUNICORN_WORKERS", None)
            else:
                os.environ["GIZMOAPP_GUNICORN_WORKERS"] = previous

    def test_gunicorn_config_uses_memory_backed_heartbeats_and_no_shared_control_socket(self):
        config = runpy.run_path(str(ROOT_DIR / "deploy" / "gunicorn.conf.py"))
        if Path("/dev/shm").is_dir():
            self.assertEqual(config["worker_tmp_dir"], "/dev/shm")
        self.assertTrue(config["control_socket_disable"])


if __name__ == "__main__":
    unittest.main()
