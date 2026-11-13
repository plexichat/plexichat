#!/usr/bin/env python3
"""Fail when a Python package cannot be committed because .gitignore hides it.

``src/core/selftest/services/data/`` was added during a refactor and never
landed. The root ``.gitignore`` line ``data/`` matches any directory with that
name, so ``git add`` skipped all five files without a word, and nothing caught
it: the tests that import the package passed locally, where the files exist on
disk, and CI stayed green because the package it never received was never
imported there either. The split simply did not ship.

A package is a directory holding ``__init__.py``. If git ignores that file, or
any parent of it, the package is invisible to every clone -- including to the
new files someone adds to it later -- so this is an error, not a warning.

Usage::

    python ci-tooling/check_gitignore_packages.py
    python ci-tooling/check_gitignore_packages.py --root src --root ci-tooling

Exit codes: 0 = every package is committable, 1 = at least one is ignored.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import List, Sequence, Tuple

DEFAULT_ROOTS = ("src", "ci-tooling", "scripts", "docker")
# Directories that are never part of the shipped source tree. .venv holds
# installed packages by the thousand and would drown the real findings.
SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "build",
    "dist",
    ".cache",
    ".git",
}


def find_packages(roots: Sequence[str]) -> List[str]:
    """Directories under the roots that contain an ``__init__.py``."""
    packages: List[str] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            if "__init__.py" in filenames:
                packages.append(os.path.normpath(dirpath))
    return sorted(set(packages))


def ignore_rule(init_path: str) -> str:
    """The .gitignore rule matching ``init_path``, or "" when it is committable.

    ``--no-index`` asks git to judge the *rules* rather than the index, which is
    what matters here: a package whose files are tracked but whose directory is
    ignored silently drops every new file added to it.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", init_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    first = (result.stdout or "").splitlines()
    return first[0] if first else "ignored"


def audit(roots: Sequence[str]) -> List[Tuple[str, str]]:
    """(package, rule) for every package git would refuse to commit."""
    findings: List[Tuple[str, str]] = []
    for package in find_packages(roots):
        rule = ignore_rule(os.path.join(package, "__init__.py"))
        if rule:
            findings.append((package, rule))
    return findings


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help=f"directory to scan (repeatable; default: {' '.join(DEFAULT_ROOTS)})",
    )
    args = parser.parse_args(argv)
    roots = tuple(args.roots) if args.roots else DEFAULT_ROOTS

    findings = audit(roots)
    if findings:
        for package, rule in findings:
            print(
                f"FAIL: {package}/ is a Python package that git ignores",
                file=sys.stderr,
            )
            print(f"      {rule}", file=sys.stderr)
        print(
            "      Negate the path in .gitignore (e.g. `!src/your/package/`) so the"
            " package can be committed, or rename it out of the ignored pattern.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: every Python package under {', '.join(roots)} is committable")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
