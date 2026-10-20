import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import remote_exec  # noqa: E402


class RemoteExecTests(unittest.TestCase):
    def test_build_wrapper_quotes_args_env_and_cwd(self):
        wrapper = remote_exec.build_wrapper(
            remote_dir="/home/example/CAIDA Concierge",
            remote_payload="/tmp/caida-remote-exec.abc123/payload.py",
            mode="python",
            python_bin="python3",
            env={
                "URL": "https://example.test/a?b=1&c=(two)",
                "EMPTY": "",
            },
            payload_args=["arg with spaces", "quote'arg", "semi;colon"],
        )

        self.assertIn("cd '/home/example/CAIDA Concierge'", wrapper)
        self.assertIn("export URL='https://example.test/a?b=1&c=(two)'", wrapper)
        self.assertIn("export EMPTY=''", wrapper)
        self.assertIn("'arg with spaces'", wrapper)
        self.assertIn("'quote'\"'\"'arg'", wrapper)
        self.assertIn("'semi;colon'", wrapper)

    def test_parse_env_validates_names(self):
        self.assertEqual(remote_exec.parse_env(["GOOD=value"]), {"GOOD": "value"})
        with self.assertRaises(SystemExit):
            remote_exec.parse_env(["BAD-NAME=value"])
        with self.assertRaises(SystemExit):
            remote_exec.parse_env(["MISSING_VALUE"])

    def test_resolve_mode_detects_python_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            python_by_suffix = tmp_path / "snippet.py"
            python_by_suffix.write_text("print('hello')\n", encoding="utf-8")
            python_by_shebang = tmp_path / "snippet"
            python_by_shebang.write_text("#!/usr/bin/env python3\nprint('hello')\n", encoding="utf-8")
            bash_payload = tmp_path / "snippet.sh"
            bash_payload.write_text("echo hello\n", encoding="utf-8")

            self.assertEqual(remote_exec.resolve_mode("auto", python_by_suffix), "python")
            self.assertEqual(remote_exec.resolve_mode("auto", python_by_shebang), "python")
            self.assertEqual(remote_exec.resolve_mode("auto", bash_payload), "bash")
            self.assertEqual(remote_exec.resolve_mode("bash", python_by_suffix), "bash")

    def test_build_bootstrap_streams_payload_and_cleans_up(self):
        bootstrap = remote_exec.build_bootstrap(
            remote_tmp="/tmp/caida-remote-exec.abc123",
            remote_payload="/tmp/caida-remote-exec.abc123/payload.sh",
            remote_wrapper="/tmp/caida-remote-exec.abc123/run.sh",
            payload_bytes=b"echo '$URL'\n",
            wrapper_bytes=b"exec /bin/bash /tmp/caida-remote-exec.abc123/payload.sh\n",
            keep_remote=False,
        )

        self.assertIn("mkdir -m 700 /tmp/caida-remote-exec.abc123", bootstrap)
        self.assertIn("trap 'rm -rf /tmp/caida-remote-exec.abc123' EXIT", bootstrap)
        self.assertIn("base64 -d > /tmp/caida-remote-exec.abc123/payload.sh", bootstrap)
        self.assertNotIn("echo '$URL'", bootstrap)

    def test_safe_payload_suffix(self):
        self.assertEqual(remote_exec.safe_payload_suffix(".py"), ".py")
        self.assertEqual(remote_exec.safe_payload_suffix(".tar.gz"), ".tar.gz")
        self.assertEqual(remote_exec.safe_payload_suffix(".bad suffix"), "")

    def test_strip_remainder_separator(self):
        self.assertEqual(remote_exec.strip_remainder_separator(["--", "--flag"]), ["--flag"])
        self.assertEqual(remote_exec.strip_remainder_separator(["plain"]), ["plain"])


if __name__ == "__main__":
    unittest.main()
