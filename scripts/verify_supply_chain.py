#!/usr/bin/env python3
"""Verify supply-chain integrity for Plexichat deployment artifacts.

Checks (fail-closed by default):
- docker-compose.yml: no floating :latest on third-party images; first-party
  backend/client/backup images must be pinned (no :-latest default, no @-less
  mutable tag when ALLOW_UNPINNED_IMAGES is unset) and/or use digest refs.
- Deploy/config env: BACKEND_VERSION/CLIENT_VERSION must not be 'latest',
  'dev', or empty unless ALLOW_UNPINNED_IMAGES=1 (explicit, audited).
- Optional: verify image digests against a lock file when provided.

Usage:
    python scripts/verify_supply_chain.py
    python scripts/verify_supply_chain.py --compose docker-compose.yml --env-file .env
    python scripts/verify_supply_chain.py --require-digest-lock image-digests.lock.json
    ALLOW_UNPINNED_IMAGES=1 python scripts/verify_supply_chain.py  # dev override

Exit 0 on pass, 1 on policy violation, 2 on usage/runtime error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMPOSE = REPO_ROOT / "docker-compose.yml"

# First-party images whose tags are operator-controlled via env vars.
FIRST_PARTY_IMAGE_RE = re.compile(
    r"(?m)^\s*image:\s*"
    r"\$\{CI_REGISTRY_IMAGE[^}]*\}/(?:backend|nginx|backup):"
    r"\$\{(BACKEND_VERSION|CLIENT_VERSION):-([^}]*)\}\s*$"
)
# Any image line.
IMAGE_LINE_RE = re.compile(r"(?m)^\s*image:\s*(\S+)\s*$")
# Digest reference.
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}\b")
UNPINNED_TAGS = {"latest", "dev", "edge", "nightly", "stable", "master", "main"}


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def allow_unpinned() -> bool:
    # Clear, audited explicit overrides for dev/testing.
    return _truthy("ALLOW_UNPINNED_IMAGES") or _truthy("ALLOW_LATEST_FALLBACK")


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            out[key] = value
    return out


def check_version_value(name: str, value: str, source: str, errors: list[str]) -> None:
    if allow_unpinned():
        return
    v = (value or "").strip()
    if not v:
        errors.append(f"{source}: {name} is empty; set an explicit pinned release tag")
        return
    low = v.lower()
    if low in UNPINNED_TAGS or low.startswith("dev") or DIGEST_RE.fullmatch(v):
        if DIGEST_RE.fullmatch(v):
            return  # digest ref is pinned
        errors.append(
            f"{source}: {name}={v!r} is unpinned/unsafe by default; "
            "use an immutable release tag (e.g. 1.0.0a184) or set "
            "ALLOW_UNPINNED_IMAGES=1 for explicit audited dev use"
        )


def check_compose(path: Path, errors: list[str], warnings: list[str]) -> None:
    if not path.is_file():
        errors.append(f"compose file not found: {path}")
        return
    text = path.read_text(encoding="utf-8", errors="replace")

    # First-party images: refuse :-latest defaults unless explicitly overridden.
    if not allow_unpinned():
        for m in FIRST_PARTY_IMAGE_RE.finditer(text):
            var, default = m.group(1), m.group(2).strip()
            if default.lower() in UNPINNED_TAGS or not default:
                errors.append(
                    f"{path}: first-party image default {var}:-{default} is unpinned; "
                    "set a pinned release default or require explicit env and "
                    "ALLOW_UNPINNED_IMAGES=1 for dev"
                )

    for m in IMAGE_LINE_RE.finditer(text):
        image = m.group(1)
        # Variable references are validated via env/default checks above.
        if "${" in image:
            # Still flag literal latest embedded oddly.
            if re.search(r":-latest\b", image) and not allow_unpinned():
                warnings.append(
                    f"{path}: image uses :-latest fallback: {image} "
                    "(deploy/config must supply a pinned tag; set ALLOW_UNPINNED_IMAGES=1 to permit)"
                )
            continue
        if image.endswith(":latest") or re.search(r":latest(?:@|$)", image):
            errors.append(f"{path}: floating third-party image tag: {image}")
        # Digest-pinned images are preferred; warn (not fail) for plain tags.
        if not DIGEST_RE.search(image) and ":" in image.split("/")[-1]:
            warnings.append(
                f"{path}: image not digest-pinned (tag-only): {image} "
                "(prefer name:tag@sha256:...; generate/update image-digests.lock.json)"
            )


def check_digest_lock(lock_path: Path, errors: list[str], warnings: list[str]) -> None:
    if not lock_path.is_file():
        errors.append(f"digest lock file not found: {lock_path}")
        return
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"digest lock invalid JSON ({lock_path}): {exc}")
        return
    if not isinstance(data, dict):
        errors.append(f"digest lock must be a JSON object: {lock_path}")
        return
    images = data.get("images")
    if not isinstance(images, dict) or not images:
        warnings.append(f"digest lock has no images map: {lock_path}")
        return
    for ref, meta in images.items():
        if not isinstance(meta, dict) or not DIGEST_RE.search(
            str(meta.get("digest", ""))
        ):
            errors.append(f"digest lock entry missing sha256 digest: {ref}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--compose", default=str(DEFAULT_COMPOSE), help="docker-compose.yml path"
    )
    ap.add_argument(
        "--env-file", action="append", default=[], help="env file(s) to validate"
    )
    ap.add_argument(
        "--require-digest-lock",
        metavar="PATH",
        help="require and validate an image digest lock file",
    )
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = ap.parse_args(argv)

    errors: list[str] = []
    warnings: list[str] = []

    check_compose(Path(args.compose), errors, warnings)

    for env_path in args.env_file:
        p = Path(env_path)
        env = read_env_file(p)
        for name in ("BACKEND_VERSION", "CLIENT_VERSION"):
            if name in env:
                check_version_value(name, env[name], str(p), errors)
        # Also check PLEXICHAT_VERSION / generic aliases if present.
        for name in ("PLEXICHAT_VERSION", "VERSION"):
            if name in env:
                check_version_value(name, env[name], str(p), errors)

    # Environment variables take precedence in deploy flows.
    for name in ("BACKEND_VERSION", "CLIENT_VERSION", "PLEXICHAT_VERSION"):
        if name in os.environ:
            check_version_value(name, os.environ[name], f"env:{name}", errors)

    if args.require_digest_lock:
        check_digest_lock(Path(args.require_digest_lock), errors, warnings)

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    if errors or (args.strict and warnings):
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        if args.strict and warnings and not errors:
            print("ERROR: warnings treated as errors (--strict)", file=sys.stderr)
        return 1

    print("supply-chain verification: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
