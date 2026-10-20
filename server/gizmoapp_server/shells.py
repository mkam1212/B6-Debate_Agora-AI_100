from __future__ import annotations

import subprocess
from pathlib import Path

AUTO_SHELL = "auto"
DEFAULT_SHELL = AUTO_SHELL
FALLBACK_SHELL = "graphical"

TEXT_SHELL_PATHS = (
    "server/gizmoapp_server/templates/index_text.html",
    "server/gizmoapp_server/static/app/text/",
)
GRAPHICAL_SHELL_PATHS = (
    "server/gizmoapp_server/templates/index.html",
    "server/gizmoapp_server/static/app/main.js",
    "server/gizmoapp_server/static/app/scene.js",
    "server/gizmoapp_server/static/app/styles.css",
)

SHELL_DEFINITIONS = {
    "graphical": {
        "slug": "graphical",
        "label": "Graphical",
        "description": "Blank light canvas shell ready for sprites, animation, or future 3D.",
        "template": "index.html",
        "asset_subpath": "app/",
        "theme_color": "#f6f5ef",
        "background_color": "#f6f5ef",
    },
    "text": {
        "slug": "text",
        "label": "Text",
        "description": "Blank text-first shell ready for forms, lists, dashboards, or workflows.",
        "template": "index_text.html",
        "asset_subpath": "app/text/",
        "theme_color": "#f6f5ef",
        "background_color": "#f6f5ef",
    },
}


def _matches_any(path: str, candidates: tuple[str, ...]) -> bool:
    return any(path == candidate.rstrip("/") or path.startswith(candidate) for candidate in candidates)


def classify_shell_paths(paths: set[str]) -> str | None:
    text_changed = any(_matches_any(path, TEXT_SHELL_PATHS) for path in paths)
    graphical_changed = any(_matches_any(path, GRAPHICAL_SHELL_PATHS) for path in paths)
    if text_changed and not graphical_changed:
        return "text"
    if graphical_changed and not text_changed:
        return "graphical"
    return None


def _paths_from_status(output: str) -> set[str]:
    paths: set[str] = set()
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path:
            paths.add(path)
    return paths


def _git_paths(repo_root: Path, args: list[str], *, status_output: bool = False) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return set()
    if status_output:
        return _paths_from_status(result.stdout)
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def changed_paths_since_base(repo_root: Path) -> set[str]:
    paths = set()
    paths.update(_git_paths(repo_root, ["status", "--porcelain"], status_output=True))
    paths.update(_git_paths(repo_root, ["diff", "--name-only", "HEAD"]))
    paths.update(_git_paths(repo_root, ["diff", "--name-only", "origin/main...HEAD"]))
    return paths


def resolve_shell_variant(shell_variant: str | None, repo_root: Path | None = None) -> str:
    variant = (shell_variant or DEFAULT_SHELL).strip().lower()
    if variant in SHELL_DEFINITIONS:
        return variant
    if variant != AUTO_SHELL:
        choices = ", ".join(available_shell_choices())
        raise ValueError(f"Unknown shell {variant!r}; expected one of: {choices}.")
    if repo_root is not None:
        detected = classify_shell_paths(changed_paths_since_base(repo_root))
        if detected is not None:
            return detected
    return FALLBACK_SHELL


def get_shell_definition(shell_variant: str | None, repo_root: Path | None = None) -> dict:
    variant = resolve_shell_variant(shell_variant, repo_root)
    return SHELL_DEFINITIONS[variant].copy()


def shell_settings(shell_variant: str | None, repo_root: Path | None = None) -> dict:
    definition = get_shell_definition(shell_variant, repo_root)
    return {
        "APP_SHELL_REQUESTED": (shell_variant or DEFAULT_SHELL).strip().lower(),
        "APP_SHELL": definition["slug"],
        "APP_SHELL_LABEL": definition["label"],
        "APP_SHELL_DESCRIPTION": definition["description"],
        "APP_SHELL_TEMPLATE": definition["template"],
        "APP_SHELL_ASSET_SUBPATH": definition["asset_subpath"],
        "THEME_COLOR": definition["theme_color"],
        "BACKGROUND_COLOR": definition["background_color"],
    }


def available_shells() -> list[dict]:
    return [
        {
            "slug": definition["slug"],
            "label": definition["label"],
            "description": definition["description"],
        }
        for definition in SHELL_DEFINITIONS.values()
    ]


def available_shell_choices() -> list[str]:
    return [AUTO_SHELL, *sorted(SHELL_DEFINITIONS.keys())]
