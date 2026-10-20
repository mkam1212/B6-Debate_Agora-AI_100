#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_NAME = "Codex"
DEFAULT_EMAIL = "codex@local"


def run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result


def git_output(repo: Path, args: list[str]) -> str:
    return run_git(repo, args).stdout.strip()


def repo_root(repo: Path) -> Path:
    return Path(git_output(repo, ["rev-parse", "--show-toplevel"]))


def git_path(repo: Path, name: str) -> Path:
    raw_path = Path(git_output(repo, ["rev-parse", "--git-path", name]))
    if raw_path.is_absolute():
        return raw_path
    return repo_root(repo) / raw_path


def get_local_config(repo: Path, key: str) -> str:
    result = run_git(repo, ["config", "--local", "--get", key], check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def set_local_config(repo: Path, key: str, value: str) -> None:
    run_git(repo, ["config", "--local", key, value])


def ensure_identity(repo: Path, fix: bool) -> list[str]:
    messages: list[str] = []
    name = get_local_config(repo, "user.name")
    email = get_local_config(repo, "user.email")

    if not name:
        if not fix:
            messages.append(f"Missing repo-local user.name; run make commit-ready to set {DEFAULT_NAME!r}.")
        else:
            set_local_config(repo, "user.name", DEFAULT_NAME)
            messages.append(f"Set repo-local user.name to {DEFAULT_NAME!r}.")

    if not email:
        if not fix:
            messages.append(f"Missing repo-local user.email; run make commit-ready to set {DEFAULT_EMAIL!r}.")
        else:
            set_local_config(repo, "user.email", DEFAULT_EMAIL)
            messages.append(f"Set repo-local user.email to {DEFAULT_EMAIL!r}.")

    if name and email:
        messages.append(f"Repo-local Git identity is configured as {name} <{email}>.")

    return messages


def check_index_lock(repo: Path, fix: bool, stale_seconds: int) -> tuple[list[str], bool]:
    lock_path = git_path(repo, "index.lock")
    if not lock_path.exists():
        return [f"No Git index lock found at {lock_path}."], False

    age_seconds = max(0, int(time.time() - lock_path.stat().st_mtime))
    if age_seconds < stale_seconds:
        return (
            [
                f"Git index lock exists at {lock_path} and is only {age_seconds}s old.",
                "Do not remove a fresh lock automatically; wait for the other Git process or inspect it.",
            ],
            True,
        )

    if not fix:
        return (
            [
                f"Git index lock exists at {lock_path} and appears stale ({age_seconds}s old).",
                "Run make commit-ready to remove stale locks safely before committing.",
            ],
            True,
        )

    lock_path.unlink()
    return [f"Removed stale Git index lock at {lock_path} ({age_seconds}s old)."], False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a checkout for a local agent commit.")
    parser.add_argument("--repo", type=Path, default=ROOT_DIR, help="Repository root to inspect.")
    parser.add_argument("--fix", action="store_true", help="Set missing repo-local identity and remove stale locks.")
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=600,
        help="Only remove index.lock files at least this old when --fix is used.",
    )
    args = parser.parse_args(argv)

    try:
        repo = repo_root(args.repo)
        messages = ensure_identity(repo, args.fix)
        lock_messages, blocked = check_index_lock(repo, args.fix, args.stale_seconds)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for message in [*messages, *lock_messages]:
        print(message)

    if blocked:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
