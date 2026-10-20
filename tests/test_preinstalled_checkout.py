from pathlib import Path
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def copy_installer_surface(checkout, installer=None):
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    for name in ("envfile.py", "require_explicit_approval.sh"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    shutil.copyfile(installer or ROOT / "scripts/install_checkout.sh", scripts / "install_checkout.sh")
    (checkout / ".env.example").write_text("GIZMOAPP_SHELL=text\n")
    (checkout / "server").mkdir()
    (checkout / "server/requirements.txt").write_text("flask==3.1.1\n")
    return scripts / "install_checkout.sh"


class PreinstalledCheckoutTests(unittest.TestCase):
    def run_installer(self, *, preinstalled, readonly=True, shape="shim", target="absolute", env_override=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "checkout"
            installer = copy_installer_surface(checkout)
            runtime = root / "runtime"
            binary = runtime / "bin" / "python"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DEPENDENCY_TEST_LOG\"\n")
            binary.chmod(0o555 if readonly else 0o755)
            venv = checkout / ".venv"
            if shape == "venv-link":
                venv.symlink_to(runtime)
            else:
                (venv / "bin").mkdir(parents=True)
                shim = venv / "bin/python"
                if shape == "python-link":
                    shim.symlink_to(binary)
                else:
                    shim.write_text("#!/bin/sh\nexec " + shlex.quote(str(binary)) + ' "$@"\n')
                    shim.chmod(0o755)
            if target == "relative":
                selected_python = "runtime/bin/python"
            elif target == "missing":
                selected_python = str(root / "absent/bin/python")
            else:
                selected_python = str(binary)
            if env_override:
                (checkout / ".env").write_text(
                    "GIZMOAPP_SHELL=text\nCODINGWORKSPACE_PREINSTALLED_DEPENDENCIES=0\n"
                    "CODINGWORKSPACE_PREINSTALLED_PYTHON=/absent/python\n"
                )
            log = root / "calls"
            environment = {**os.environ, "ALLOW_NETWORK_INSTALL": "1",
                           "CODINGWORKSPACE_PREINSTALLED_DEPENDENCIES": str(preinstalled),
                           "CODINGWORKSPACE_PREINSTALLED_PYTHON": selected_python,
                           "DEPENDENCY_TEST_LOG": str(log)}
            result = subprocess.run(["bash", str(installer)], env=environment,
                                    text=True, capture_output=True)
            calls = log.read_text() if log.exists() else ""
            return result, calls

    def test_shared_dependencies_skip_pip_but_initialize_project(self):
        result, calls = self.run_installer(preinstalled=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("pip install", calls)
        self.assertIn("init-db", calls)
        self.assertIn("describe --shell text", calls)

    def test_preinstalled_flag_refuses_writable_environment(self):
        result, calls = self.run_installer(preinstalled=1, readonly=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("read-only image Python", result.stderr)
        self.assertEqual(calls, "")

    def test_ordinary_checkout_still_installs_requirements(self):
        result, calls = self.run_installer(preinstalled=0, readonly=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-m pip install -r", calls)
        self.assertIn("init-db", calls)

    def test_preinstalled_flag_refuses_links_and_invalid_targets(self):
        for options in ({"shape": "venv-link"}, {"shape": "python-link"},
                        {"target": "relative"}, {"target": "missing"}):
            with self.subTest(**options):
                result, calls = self.run_installer(preinstalled=1, **options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("managed Python shim", result.stderr)
                self.assertEqual(calls, "")

    def test_project_env_cannot_replace_selected_runtime(self):
        result, calls = self.run_installer(preinstalled=1, env_override=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("pip install", calls)
        self.assertIn("init-db", calls)

    def test_prior_release_rebuilds_shim_when_new_image_target_is_missing(self):
        spec = importlib.util.spec_from_file_location(
            "prior_dependency_readiness", FIXTURES / "codingworkspace_591eb44_venv.py"
        )
        prior = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = prior
        try:
            spec.loader.exec_module(prior)
            prior.checkout_dependency_fingerprint = lambda workspace: "dependency"
            prior.checkout_dependency_runtime_fingerprint = lambda settings: "prior-runtime"
            prior.checkout_install_script = lambda workspace: workspace / "scripts/install_checkout.sh"
            prior.checkout_dependency_stamp_path = lambda workspace: workspace / ".venv/.codingworkspace-dependencies.json"
            prior.MAX_CHECKOUT_DEPENDENCY_STAMP_BYTES = 65536
            prior.read_text_no_symlink = lambda path, **kwargs: path.read_text()
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                checkout = root / "checkout"
                installer = copy_installer_surface(
                    checkout, FIXTURES / "gizmoapp_7dbc70b_install_checkout.sh"
                )
                shim = checkout / ".venv/bin/python"
                shim.parent.mkdir(parents=True)
                missing_target = root / "new-image-only/venv/bin/python"
                shim.write_text("#!/bin/sh\nexec " + shlex.quote(str(missing_target)) + ' "$@"\n')
                shim.chmod(0o755)
                self.assertFalse(missing_target.exists())
                self.assertTrue(shim.exists())
                readiness = prior.checkout_dependency_readiness(checkout)
                self.assertFalse(readiness.ready)
                self.assertEqual(readiness.reason, "legacy-stamp")
                self.assertTrue(prior._clear_checkout_virtualenv(checkout))
                self.assertFalse((checkout / ".venv").exists())
                self.assertFalse(missing_target.parent.exists())
                # A local fake venv builder records installer behavior; no pip or
                # network operation is executed by the prior checkout installer.
                fake_bin = root / "fake-bin"
                fake_bin.mkdir()
                fake_python = fake_bin / "python3"
                fake_python.write_text(
                    "#!" + sys.executable + "\n"
                    "import os, pathlib, sys\n"
                    'if sys.argv[1:3] == ["-m", "venv"]:\n'
                    '    binary = pathlib.Path(sys.argv[3]) / "bin/python"\n'
                    '    binary.parent.mkdir(parents=True)\n'
                    '    binary.write_text(' + repr(
                        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DEPENDENCY_TEST_LOG\"\n"
                    ) + ')\n'
                    '    binary.chmod(0o755)\n'
                    'else:\n'
                    '    os.execv(' + repr(sys.executable) + ', [' + repr(sys.executable)
                    + ', *sys.argv[1:]])\n'
                )
                fake_python.chmod(0o755)
                log = root / "calls"
                environment = {**os.environ, "ALLOW_NETWORK_INSTALL": "1",
                               "CODINGWORKSPACE_PREINSTALLED_DEPENDENCIES": "0",
                               "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
                               "DEPENDENCY_TEST_LOG": str(log)}
                result = subprocess.run(["bash", str(installer)], env=environment,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(shim.is_file())
                self.assertFalse(shim.is_symlink())
                calls = log.read_text()
                self.assertIn("-m pip install -r", calls)
                self.assertIn("init-db", calls)
        finally:
            sys.modules.pop(spec.name, None)
