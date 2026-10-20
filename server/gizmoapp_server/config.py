from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from .shells import AUTO_SHELL, DEFAULT_SHELL, available_shell_choices, available_shells, shell_settings

ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<raw>.*)$"
)
SHELL_INTENT_PATH = Path("deploy/app-shell.txt")
FEATURE_INTENT_PATH = Path("deploy/features.txt")
FEATURE_CHOICES = frozenset(
    {
        "admin",
        "audio",
        "machine-learning",
        "mapping",
        "optimization",
        "sample-nodes",
        "search",
    }
)
URL_PREFIX_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]*$")
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def parse_env_assignment(line: str, line_number: int) -> tuple[str, str] | None:
    match = ASSIGNMENT_RE.match(line)
    if match is None:
        return None

    raw_value = match.group("raw").strip()
    if raw_value == "":
        return match.group("key"), ""

    try:
        parts = shlex.split(raw_value, posix=True)
    except ValueError as exc:
        raise RuntimeError(f"Invalid env syntax on line {line_number}: {exc}") from exc

    if len(parts) != 1:
        raise RuntimeError(
            f"Invalid env syntax on line {line_number}: expected one value token."
        )

    return match.group("key"), parts[0]


def load_local_env(env_path: Path, environ: dict[str, str] | None = None) -> None:
    if not env_path.exists():
        return

    target = environ if environ is not None else os.environ
    for index, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        assignment = parse_env_assignment(line, index)
        if assignment is None:
            raise RuntimeError(f"Invalid env syntax on line {index}: expected KEY=VALUE.")

        key, value = assignment
        target.setdefault(key, value)


def read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for index, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assignment = parse_env_assignment(line, index)
        if assignment is None:
            raise RuntimeError(f"Invalid env syntax on line {index}: expected KEY=VALUE.")
        key, value = assignment
        if key in values:
            raise RuntimeError(f"Duplicate env key {key!r} on line {index}.")
        values[key] = value
    return values


def effective_environment(repo_root: Path) -> dict[str, str]:
    values = read_env_file(repo_root / "deploy" / "app.env")
    values.update(read_env_file(repo_root / ".env"))
    values.update(os.environ)
    return values


def normalize_url_prefix(value: str | None) -> str:
    if value is None:
        return ""

    trimmed = value.strip()
    if trimmed in {"", "/"}:
        return ""

    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed}"
    normalized = trimmed.rstrip("/")
    segments = normalized[1:].split("/")
    if any(
        segment in {"", ".", ".."} or URL_PREFIX_SEGMENT_RE.fullmatch(segment) is None
        for segment in segments
    ):
        raise RuntimeError(
            "GIZMOAPP_URL_PREFIX must contain only safe path segments, such as /demo-app."
        )
    return normalized


def load_shell_intent(repo_root: Path) -> str | None:
    intent_path = repo_root / SHELL_INTENT_PATH
    try:
        lines = intent_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    valid = set(available_shell_choices())
    for line in lines:
        value = line.split("#", 1)[0].strip().lower()
        if not value:
            continue
        if value not in valid:
            choices = ", ".join(sorted(valid))
            raise RuntimeError(f"Invalid shell intent {value!r}; expected one of: {choices}.")
        return value
    return None


def load_feature_intent(repo_root: Path) -> frozenset[str]:
    intent_path = repo_root / FEATURE_INTENT_PATH
    try:
        lines = intent_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return frozenset()
    except OSError as exc:
        raise RuntimeError(f"Could not read {intent_path}: {exc}") from exc

    enabled: set[str] = set()
    for line in lines:
        content = line.split("#", 1)[0].strip().lower()
        if not content:
            continue
        enabled.update(part for part in re.split(r"[\s,]+", content) if part)

    invalid = sorted(enabled - FEATURE_CHOICES)
    if invalid:
        choices = ", ".join(sorted(FEATURE_CHOICES))
        raise RuntimeError(
            f"Invalid feature intent {', '.join(invalid)}; expected values from: {choices}."
        )
    return frozenset(enabled)


def _parse_bool(value: str | None, *, name: str, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} must be one of 1/0, true/false, yes/no, or on/off.")


def _parse_int(value: str | None, *, name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = default if value is None or not value.strip() else int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def select_shell_setting(shell_variant: str | None, repo_root: Path, environ: dict[str, str]) -> str:
    requested_shell = shell_variant if shell_variant is not None else environ.get("GIZMOAPP_SHELL", DEFAULT_SHELL)
    if requested_shell.strip().lower() == AUTO_SHELL:
        tracked_shell = load_shell_intent(repo_root)
        if tracked_shell and tracked_shell != AUTO_SHELL:
            return tracked_shell
    return requested_shell


def load_settings(shell_variant: str | None = None, repo_root: Path | None = None) -> dict:
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    environ = effective_environment(repo_root)
    data_dir = repo_root / "var" / "data"
    log_dir = repo_root / "var" / "log"
    static_dir = Path(__file__).resolve().parent / "static"
    url_prefix = normalize_url_prefix(environ.get("GIZMOAPP_URL_PREFIX", ""))
    configured_db_path = Path(environ.get("GIZMOAPP_DB_PATH", str(data_dir / "gizmoapp.sqlite3"))).expanduser()
    db_path = configured_db_path if configured_db_path.is_absolute() else repo_root / configured_db_path
    app_environment = environ.get("GIZMOAPP_ENV", "development").strip().lower()
    if app_environment not in {"development", "production", "test"}:
        raise RuntimeError("GIZMOAPP_ENV must be development, production, or test.")
    sqlite_journal_mode = environ.get("GIZMOAPP_SQLITE_JOURNAL_MODE", "DELETE").strip().upper()
    if sqlite_journal_mode not in {"DELETE", "TRUNCATE", "PERSIST", "WAL"}:
        raise RuntimeError("GIZMOAPP_SQLITE_JOURNAL_MODE must be DELETE, TRUNCATE, PERSIST, or WAL.")
    sqlite_synchronous = environ.get("GIZMOAPP_SQLITE_SYNCHRONOUS", "FULL").strip().upper()
    if sqlite_synchronous not in {"NORMAL", "FULL", "EXTRA"}:
        raise RuntimeError("GIZMOAPP_SQLITE_SYNCHRONOUS must be NORMAL, FULL, or EXTRA.")
    secret_key = environ.get("GIZMOAPP_SECRET_KEY", "dev-only-secret")
    if app_environment == "production" and secret_key in {"", "dev-only-secret", "change-me-before-production"}:
        raise RuntimeError("GIZMOAPP_SECRET_KEY must be set to a unique value in production.")

    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    settings = {
        "APP_NAME": environ.get("GIZMOAPP_APP_NAME", "GizmoApp"),
        "APP_TAGLINE": "Agentic coding starter",
        "APP_ENV": app_environment,
        "URL_PREFIX": url_prefix,
        "DB_PATH": db_path,
        "SQLITE_JOURNAL_MODE": sqlite_journal_mode,
        "SQLITE_SYNCHRONOUS": sqlite_synchronous,
        "REPO_ROOT": repo_root,
        "STATIC_ROOT": static_dir,
        "SECRET_KEY": secret_key,
        "AUTO_MIGRATE": _parse_bool(
            environ.get("GIZMOAPP_AUTO_MIGRATE"),
            name="GIZMOAPP_AUTO_MIGRATE",
            default=app_environment != "production",
        ),
        "TRUST_PROXY": _parse_bool(
            environ.get("GIZMOAPP_TRUST_PROXY"),
            name="GIZMOAPP_TRUST_PROXY",
            default=False,
        ),
        "MAX_CONTENT_LENGTH": _parse_int(
            environ.get("GIZMOAPP_MAX_CONTENT_LENGTH"),
            name="GIZMOAPP_MAX_CONTENT_LENGTH",
            default=1_048_576,
            minimum=16_384,
            maximum=16_777_216,
        ),
        "REQUEST_TIMEOUT_MS": _parse_int(
            environ.get("GIZMOAPP_REQUEST_TIMEOUT_MS"),
            name="GIZMOAPP_REQUEST_TIMEOUT_MS",
            default=15_000,
            minimum=1_000,
            maximum=120_000,
        ),
        "ENABLED_FEATURES": load_feature_intent(repo_root),
        "AVAILABLE_SHELLS": available_shells(),
        "ADMIN_NOTES": [
            "Admin and optional capability routes are disabled unless deploy/features.txt enables them.",
            "Frontend is build-free by design to keep deploys easy.",
            "Responsive layout should be checked across phone, tablet, laptop, and desktop widths.",
            "The project ships with both graphical and text-first frontend shells.",
            "GIZMOAPP_SHELL=auto chooses the shell from changed files; graphical/text force a shell.",
            "Machine-learning features should install scikit-learn only when requested.",
            "Mapping support is optional; for requested location-dependent features, assume UBC Vancouver only if the user gives no other location.",
        ],
    }
    settings.update(
        shell_settings(
            select_shell_setting(shell_variant, repo_root, environ),
            repo_root=repo_root,
        )
    )
    return settings


def scoped_path(url_prefix: str, path: str = "") -> str:
    cleaned = path.lstrip("/")
    base = f"{url_prefix}/" if url_prefix else "/"
    return f"{base}{cleaned}" if cleaned else base
