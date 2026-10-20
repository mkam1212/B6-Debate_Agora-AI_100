# Exact functions from CodingWorkspace 591eb44f8585398f4bcd246bc0d60743e6a16fef.
# The regression supplies unrelated fingerprint/probe helpers without executing
# student code or depending on another checkout or network access.
from __future__ import annotations
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any

@dataclass(frozen=True)
class CheckoutDependencyReadiness:
    ready: bool
    reason: str
    dependency_fingerprint: str
    runtime_fingerprint: str

def checkout_dependency_readiness(
    workspace_dir: Path,
    settings: Settings | Any | None = None,
) -> CheckoutDependencyReadiness:
    dependency_fingerprint = checkout_dependency_fingerprint(workspace_dir)
    runtime_fingerprint = checkout_dependency_runtime_fingerprint(settings)
    has_install_surface = checkout_install_script(workspace_dir).exists() or (
        workspace_dir / "server" / "requirements.txt"
    ).exists()
    if not has_install_surface:
        return CheckoutDependencyReadiness(
            True,
            "no-install-surface",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    python_bin = workspace_dir / ".venv" / "bin" / "python"
    if not python_bin.exists():
        return CheckoutDependencyReadiness(
            False,
            "missing-venv",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    stamp_path = checkout_dependency_stamp_path(workspace_dir)
    try:
        stamp = json.loads(
            read_text_no_symlink(
                stamp_path,
                maximum_bytes=MAX_CHECKOUT_DEPENDENCY_STAMP_BYTES,
            )
        )
    except FileNotFoundError:
        return CheckoutDependencyReadiness(
            False,
            "legacy-stamp",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    except (json.JSONDecodeError, OSError, RuntimeError, UnicodeError):
        return CheckoutDependencyReadiness(
            False,
            "invalid-stamp",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    if not isinstance(stamp, dict) or stamp.get("schemaVersion") != CHECKOUT_DEPENDENCY_STAMP_SCHEMA:
        return CheckoutDependencyReadiness(
            False,
            "legacy-stamp",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    # Check runtime compatibility before dependency inputs. If both changed, an
    # old-ABI virtualenv must be rebuilt rather than incrementally updated.
    if stamp.get("runtimeFingerprint") != runtime_fingerprint:
        return CheckoutDependencyReadiness(
            False,
            "runtime-changed",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    if stamp.get("fingerprint") != dependency_fingerprint:
        return CheckoutDependencyReadiness(
            False,
            "dependency-inputs-changed",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    # Never execute a mutable checkout interpreter without the configured child
    # boundary. Callers without deployment settings can still compare the
    # installation and runtime fingerprints, but cannot claim a live probe.
    if settings is None:
        return CheckoutDependencyReadiness(
            True,
            "ready",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    if not _python_imports_available(
        python_bin,
        checkout_required_imports(workspace_dir),
        settings=settings,
        workspace_dir=workspace_dir,
    ):
        return CheckoutDependencyReadiness(
            False,
            "imports-unavailable",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    if not _pip_check_passes(python_bin, settings=settings, workspace_dir=workspace_dir):
        return CheckoutDependencyReadiness(
            False,
            "pip-check-failed",
            dependency_fingerprint,
            runtime_fingerprint,
        )
    return CheckoutDependencyReadiness(
        True,
        "ready",
        dependency_fingerprint,
        runtime_fingerprint,
    )

def _clear_checkout_virtualenv(workspace_dir: Path) -> bool:
    """Descriptor-relatively remove only ``workspace_dir/.venv`` without following links."""

    workspace_dir = Path(os.path.abspath(workspace_dir))
    virtualenv = workspace_dir / ".venv"
    if virtualenv.name != ".venv" or virtualenv.parent != workspace_dir:
        raise RuntimeError("Checkout virtualenv cleanup target is not exact")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(workspace_dir, flags)
    try:
        try:
            details = os.stat(".venv", dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise RuntimeError("Checkout virtualenv cleanup target is not a real directory")
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            raise RuntimeError("Safe checkout virtualenv cleanup is unavailable on this platform")
        shutil.rmtree(".venv", dir_fd=descriptor)
        os.fsync(descriptor)
        return True
    finally:
        os.close(descriptor)
