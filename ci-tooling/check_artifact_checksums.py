#!/usr/bin/env python3
"""Fail when a downloaded artifact or dependency has no checksum pin.

Three independent guarantees are checked:

* every ``pip install -r requirements*.txt`` in the CI files carries
  ``--require-hashes``, so pip refuses an artifact whose digest is not in the
  lock;
* every compiled lock is fully hashed (each pinned distribution has a
  ``--hash=sha256:`` entry); and
* every ``<artifact>.sha256`` sidecar in the tree matches the bytes of the
  artifact it describes, so a release cannot publish a swapped download.

A job that curls a file to disk without verifying a digest is also rejected:
the download is an artifact until it has been checked, and an unchecked one
must not be executed or shipped.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from check_ci_permissions import parse_jobs

PIP_INSTALL_RE = re.compile(r"\bpip(?:3)?\s+install\b[^|;&]*-r\s+(?P<lock>\S+?\.txt)\b")
DOWNLOAD_RE = re.compile(
    r"\b(?:curl|wget)\b[^|;&]*?(?:--output[= ]|\s-[A-Za-z]*[oO]\s+)(?P<out>\S+)"
)
# An actual checksum-verification command, not the bare word "checksum" (a
# comment or an `echo checksum` must not satisfy the gate). Require the
# verifier and its --check/-c (or openssl -verify) flag on the same line.
VERIFY_RE = re.compile(
    r"\b(?:sha256sum|sha512sum)\b[^\n]*\s(?:-c|--check)(?:\s|$)"
    r"|\bshasum\b[^\n]*\s-c(?:\s|$)"
    r"|\bopenssl\s+dgst\s+-sha256\b[^\n]*\s-verify(?:\s|$)"
)
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".npm",
    "dist",
    "test_artifacts",
}


def _lock_is_hashed(path: Path) -> list[str]:
    errors: list[str] = []
    current_has_hash = False
    current_name = ""
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        pin = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==[^#]", raw)
        if pin:
            if current_name and not current_has_hash:
                errors.append(
                    f"{path}:{lineno}: {current_name} has no --hash=sha256 entry"
                )
            current_name = pin.group(1)
            current_has_hash = False
            continue
        if re.match(r"^\s+--hash=sha256:[0-9a-f]{64}\s*\\?$", raw):
            current_has_hash = True
    if current_name and not current_has_hash:
        errors.append(
            f"{path}: no --hash=sha256 entry for the last package {current_name}"
        )
    return errors


def _sidecars(repo: Path) -> list[str]:
    errors: list[str] = []
    for sidecar in sorted(repo.rglob("*.sha256")):
        rel = sidecar.relative_to(repo)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 2:
                errors.append(f"{rel}: malformed checksum line {line!r}")
                continue
            digest, name = parts
            artifact = (sidecar.parent / Path(name).name).resolve()
            if not artifact.is_file():
                errors.append(
                    f"{rel}: artifact {name} is missing; a release would publish an unverified download"
                )
                continue
            actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if actual != digest.lower():
                errors.append(
                    f"{rel}: {name} sha256 mismatch (expected {digest}, got {actual})"
                )
    return errors


def check(repo: Path, ci_files: list[Path]) -> list[str]:
    errors: list[str] = []
    for ci in ci_files:
        path = repo / ci
        if not path.is_file():
            errors.append(f"{ci}: CI file is missing")
            continue
        for job in parse_jobs(path.read_text(encoding="utf-8")).values():
            body = job.text
            for index, line in enumerate(body.splitlines()):
                stripped = line.strip().lstrip("- ").strip()
                match = PIP_INSTALL_RE.search(stripped)
                if match and "--require-hashes" not in " ".join(
                    body.splitlines()[index : index + 4]
                ):
                    errors.append(
                        f"{ci}:{job.name}: 'pip install -r {match.group('lock')}' "
                        "must pass --require-hashes so the lock's artifact digests are enforced"
                    )
                download = DOWNLOAD_RE.search(stripped)
                if download and download.group("out") != "/dev/null":
                    if not VERIFY_RE.search(body):
                        errors.append(
                            f"{ci}:{job.name}: '{stripped}' writes a downloaded file to disk "
                            "without a sha256 verification in the same job"
                        )

    for lock in sorted(repo.glob("requirements*.txt")):
        errors.extend(_lock_is_hashed(lock))
    errors.extend(_sidecars(repo))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repository root (default: cwd)"
    )
    parser.add_argument(
        "--ci",
        action="append",
        default=None,
        help="CI file relative to --repo (repeatable)",
    )
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    ci_files = (
        [Path(ci) for ci in args.ci]
        if args.ci
        else [Path(".gitlab-ci.yml"), Path(".gitlab/ci/pipeline.yml")]
    )

    errors = check(repo, ci_files)
    if errors:
        print(
            "FAIL: a downloaded artifact or dependency has no checksum pin",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("artifact checksums OK: hashed installs and verified sidecars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
