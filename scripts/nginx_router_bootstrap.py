#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


SERVER_OPEN_RE = re.compile(r"^\s*server\s*\{\s*(?:#.*)?$")


def strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    chars: list[str] = []
    for char in line:
        if escaped:
            chars.append(char)
            escaped = False
            continue
        if char == "\\":
            chars.append(char)
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            chars.append(char)
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            chars.append(char)
            continue
        if char == "#" and not in_single and not in_double:
            break
        chars.append(char)
    return "".join(chars)


def block_matches_server_name(lines: list[str], server_name: str) -> bool:
    pattern = re.compile(r"\bserver_name\b(?P<rest>[^;]*);")
    for line in lines:
        match = pattern.search(strip_comment(line))
        if match is None:
            continue
        tokens = match.group("rest").split()
        if server_name in tokens:
            return True
    return False


def find_server_block(lines: list[str], server_name: str | None) -> tuple[int, int]:
    depth = 0
    current_start: int | None = None
    current_lines: list[str] = []
    candidate_blocks: list[tuple[int, int, list[str]]] = []

    for index, line in enumerate(lines):
        code = strip_comment(line)
        if current_start is None and depth == 0 and SERVER_OPEN_RE.match(code):
            current_start = index
            current_lines = [line]
            depth += code.count("{") - code.count("}")
            if depth == 0:
                candidate_blocks.append((current_start, index, current_lines[:]))
                current_start = None
                current_lines = []
            continue

        if current_start is not None:
            current_lines.append(line)

        depth += code.count("{") - code.count("}")

        if current_start is not None and depth == 0:
            candidate_blocks.append((current_start, index, current_lines[:]))
            current_start = None
            current_lines = []

    if server_name is not None:
        matches = [
            (start, end)
            for start, end, block_lines in candidate_blocks
            if block_matches_server_name(block_lines, server_name)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Found multiple server blocks for server_name {server_name!r}.")
        raise ValueError(f"Could not find a server block for server_name {server_name!r}.")

    if len(candidate_blocks) == 1:
        start, end, _ = candidate_blocks[0]
        return start, end

    raise ValueError(
        "Could not uniquely identify a server block. Pass --server-name to disambiguate."
    )


def ensure_managed_include(
    text: str,
    include_glob: str,
    server_name: str | None = None,
) -> tuple[str, bool]:
    include_line = f"include {include_glob};"
    if include_line in text:
        return text, False

    lines = text.splitlines(keepends=True)
    start, end = find_server_block(lines, server_name)
    indent_match = re.match(r"^(\s*)", lines[start])
    base_indent = indent_match.group(1) if indent_match else ""
    insert_line = f"{base_indent}    {include_line}\n"
    lines.insert(end, insert_line)
    return "".join(lines), True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Insert a managed GizmoApp nginx include into a server block."
    )
    parser.add_argument("server_config", type=Path)
    parser.add_argument("include_glob")
    parser.add_argument("--server-name", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    original_text = args.server_config.read_text(encoding="utf-8")
    updated_text, changed = ensure_managed_include(
        original_text,
        args.include_glob,
        server_name=args.server_name,
    )

    output_path = args.output or args.server_config
    output_path.write_text(updated_text, encoding="utf-8")
    print("updated" if changed else "already-present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
