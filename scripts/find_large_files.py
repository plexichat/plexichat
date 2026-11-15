#!/usr/bin/env python3
"""List source files over a line threshold.

The repository has grown several modules past the size where review is
practical. This walks the source trees and prints the largest offenders so the
split work can be planned instead of discovered by accident.

Usage:
    python3 scripts/find_large_files.py
    python3 scripts/find_large_files.py --lines 500 --ext .py
    python3 scripts/find_large_files.py --root src --root scripts
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_ROOTS = ("src", "scripts", "ci-tooling")
DEFAULT_EXTENSIONS = (".py", ".js", ".mjs", ".cjs")
EXCLUDE_DIRS = {
    "node_modules",
    ".venv",
    ".git",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
}


def _iter_files(roots: list[str], extensions: set[str]):
    for root in roots:
        base = Path(root)
        if not base.exists():
            print(f"skipping missing path: {root}", file=sys.stderr)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.suffix in extensions:
                    yield path


def _line_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="directory to scan (repeatable; default: src scripts tests ci-tooling)",
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=1000,
        help="minimum line count to report (default: 1000)",
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=[],
        help="extension to include (repeatable; default: .py .js .mjs .cjs)",
    )
    args = parser.parse_args(argv)

    roots: list[str] = list(args.root) if args.root else list(DEFAULT_ROOTS)
    extensions: set[str] = set(args.ext) if args.ext else set(DEFAULT_EXTENSIONS)

    rows: list[tuple[int, str]] = []
    for path in _iter_files(roots, extensions):
        count = _line_count(path)
        if count is not None and count >= args.lines:
            rows.append((count, str(path)))

    rows.sort(key=lambda item: (-item[0], item[1]))
    for count, path in rows:
        print(f"{count:6d}  {path}")

    if not rows:
        print(f"No files over {args.lines} lines.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
