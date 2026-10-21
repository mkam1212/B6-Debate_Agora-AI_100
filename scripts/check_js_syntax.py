#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import shutil
import subprocess


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_JS_ROOT = ROOT_DIR / "server" / "gizmoapp_server" / "static" / "app"
OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
CLOSE_TO_OPEN = {close: open_ for open_, close in OPEN_TO_CLOSE.items()}


@dataclass(frozen=True)
class CheckError:
    path: Path
    line: int
    column: int
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}:{self.column}: {self.message}"


def iter_js_files(paths: list[Path]) -> list[Path]:
    if not paths:
        paths = [DEFAULT_JS_ROOT]

    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.js")))
        elif path.suffix == ".js":
            files.append(path)

    return sorted({path.resolve() for path in files})


def _advance(ch: str, line: int, column: int) -> tuple[int, int]:
    if ch == "\n":
        return line + 1, 0
    return line, column + 1


def check_js_source(path: Path, source: str) -> list[CheckError]:
    errors: list[CheckError] = []

    for line_number, line_text in enumerate(source.splitlines(), start=1):
        if line_text.startswith(("<<<<<<<", "=======", ">>>>>>>")):
            errors.append(CheckError(path, line_number, 1, "merge conflict marker found"))

    stack: list[tuple[str, int, int]] = []
    state = "code"
    quote = ""
    escape = False
    token_line = 1
    token_column = 1
    line = 1
    column = 0
    index = 0

    while index < len(source):
        ch = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        display_column = column + 1

        if state == "line_comment":
            if ch == "\n":
                state = "code"

        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                line, column = _advance(ch, line, column)
                index += 1
                ch = source[index]
                state = "code"

        elif state == "string":
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                state = "code"
            elif ch == "\n" and quote != "`":
                errors.append(CheckError(path, token_line, token_column, "unterminated string literal"))
                state = "code"

        else:
            if ch == "/" and nxt == "/":
                state = "line_comment"
                line, column = _advance(ch, line, column)
                index += 1
                ch = source[index]
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                token_line = line
                token_column = display_column
                line, column = _advance(ch, line, column)
                index += 1
                ch = source[index]
            elif ch in ("'", '"', "`"):
                state = "string"
                quote = ch
                escape = False
                token_line = line
                token_column = display_column
            elif ch in OPEN_TO_CLOSE:
                stack.append((ch, line, display_column))
            elif ch in CLOSE_TO_OPEN:
                if not stack:
                    errors.append(CheckError(path, line, display_column, f"unmatched {ch!r}"))
                else:
                    open_ch, open_line, open_column = stack.pop()
                    if CLOSE_TO_OPEN[ch] != open_ch:
                        expected = OPEN_TO_CLOSE[open_ch]
                        errors.append(
                            CheckError(
                                path,
                                line,
                                display_column,
                                f"expected {expected!r} for {open_ch!r} from {open_line}:{open_column}, found {ch!r}",
                            )
                        )

        line, column = _advance(ch, line, column)
        index += 1

    if state == "block_comment":
        errors.append(CheckError(path, token_line, token_column, "unterminated block comment"))
    elif state == "string":
        errors.append(CheckError(path, token_line, token_column, "unterminated string literal"))

    for open_ch, open_line, open_column in reversed(stack):
        errors.append(CheckError(path, open_line, open_column, f"unclosed {open_ch!r}"))

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate JavaScript with Node's parser without installing packages."
    )
    parser.add_argument("paths", nargs="*", type=Path, help="JavaScript files or directories to check.")
    args = parser.parse_args(argv)

    files = iter_js_files(args.paths)
    node = shutil.which('node')
    if not node:
        print('Node.js is required for JavaScript validation. No packages were installed.', file=sys.stderr)
        return 2
    failed = False
    for path in files:
        result = subprocess.run([node, '--check', str(path)], capture_output=True, text=True)
        if result.returncode:
            print(result.stderr or result.stdout, file=sys.stderr)
            failed = True
    if failed:
        return 1
    print(f"Checked {len(files)} JavaScript file(s) with Node.js.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
