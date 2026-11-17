#!/usr/bin/env python3
"""Fail on a new token that has no entry in .secrets.baseline.

detect-secrets keeps known, reviewed findings in ``.secrets.baseline`` as SHA1
digests of the raw secret value.  The pre-commit hook only fails on findings
that are *not* in that baseline, but it needs the full detect-secrets install
(and all of its plugin heuristics) to decide what a finding is.  This gate is a
small, deterministic subset: it recognises the high-confidence token shapes
that matter for a supply-chain review, hashes the captured value exactly the
way detect-secrets does, and fails when that hash is not already baselined.

It deliberately has no third-party imports so it runs in a bare
``python:3.14-slim`` container before dependencies are installed.  A new token
must be reviewed and added to the baseline (with a rationale in the commit)
before this gate goes green again.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

# (label, pattern). Group 1, when present, is the secret value; otherwise the
# entire match is the value. The shapes mirror the plugin names in the baseline.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "AWSKeyDetector",
        re.compile(r"(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|AGPA)[A-Z0-9]{16}"),
    ),
    (
        "GitHubTokenDetector",
        re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
    ),
    ("GitLabTokenDetector", re.compile(r"glpat-[A-Za-z0-9_-]{20,}")),
    ("SlackDetector", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("StripeDetector", re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("OpenAIDetector", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    (
        "JwtTokenDetector",
        re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    (
        "PrivateKeyDetector",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
        ),
    ),
)

SKIP_DIRS = {
    ".git",
    # detect-secrets' pre-commit hook excludes tests and docs; findings there
    # are fixtures and examples, not credentials that can be leaked. Matching
    # that scope keeps this gate a strict subset of what the baseline holds.
    "tests",
    "docs",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".npm",
    "dist",
    "build",
    "test_artifacts",
    "uploads",
    ".kilo",
    ".wt-storage",
}
SKIP_SUFFIXES = (
    ".lock",
    ".pyc",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".pdf",
    ".zip",
    ".gz",
)
SKIP_FILES = {
    ".secrets.baseline",
    ".bandit-baseline.json",
    "package-lock.json",
    "pytest-shard-map.json",
}
PLACEHOLDERS = {
    "changeme",
    "example",
    "placeholder",
    "yourpassword",
    "yourtoken",
    "yourkey",
    "xxxxxxxx",
    "xxxxxxxxxxxx",
    "deadbeef",
    "0123456789abcdef",
}
ALLOWLIST_MARKER = "pragma: allowlist secret"


def _hash(secret: str) -> str:
    # Identical to detect-secrets' hash_secret: SHA1 of the utf-8 secret bytes.
    return hashlib.sha1(secret.encode("utf-8")).hexdigest()


def _baseline_digests(path: Path) -> set[str]:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        finding["hashed_secret"]
        for findings in data.get("results", {}).values()
        for finding in findings
        if "hashed_secret" in finding
    }


def _candidates(path: Path, root: Path) -> list[tuple[str, str, int, str]]:
    """Return (label, value, line, digest) for every high-confidence match."""
    found: list[tuple[str, str, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return found
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOWLIST_MARKER in line or "detect-secrets" in line:
            continue
        for label, pattern in PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(1) if match.groups() else match.group(0)
                if value.lower() in PLACEHOLDERS:
                    continue
                found.append((label, value, lineno, _hash(value)))
    _ = root
    return found


def scan(root: Path, baseline: Path) -> list[str]:
    """Return the list of new (unbaselined) findings."""
    digests = _baseline_digests(baseline)
    errors: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if path.name in SKIP_FILES or path.name.endswith(SKIP_SUFFIXES):
            continue
        for label, _value, lineno, digest in _candidates(path, root):
            if digest not in digests:
                errors.append(
                    f"{rel}:{lineno}: new {label} token (sha1 {digest}) is not in {baseline.name}"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="tree to scan (default: cwd)"
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="baseline path (default: <root>/.secrets.baseline)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    baseline = (args.baseline or (root / ".secrets.baseline")).resolve()
    if not baseline.is_file():
        print(f"FAIL: baseline {baseline} is missing", file=sys.stderr)
        return 2

    errors = scan(root, baseline)
    if errors:
        print("FAIL: new token pattern without a baseline entry", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "Review each token; only add reviewed values to .secrets.baseline.",
            file=sys.stderr,
        )
        return 1
    print("secret baseline OK: no new token patterns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
