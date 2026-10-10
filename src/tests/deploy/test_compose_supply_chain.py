"""Regression tests for Agent 54 compose supply-chain findings.

Covers, inside the ``plexichat`` repo (``docker-compose.yml`` + the docs and
generators that tell operators how to fetch compose / write ``.env``):

1. ``docker-compose fetched dev branch no checksum`` -- the compose file must
   never invite fetching ``docker-compose.yml`` from an unreleased ``dev``
   branch without an explicit ``ALLOW_DEV_COMPOSE=1`` opt-in, and must point
   at digest/checksum verification for whatever ref is fetched.
2. ``registry fallback Docker Hub different trust`` -- no silent fallback to
   ``docker.io`` (a different trust domain), no floating ``:latest``
   third-party images; any Docker Hub fallback must require explicit
   ``ALLOW_DOCKERHUB_FALLBACK=1`` opt-in.
3. ``.env default umask never chmod 600`` -- every in-repo ``.env`` write
   path (docs snippets, ``.env.example``, the docs env-generator page, and
   the compose header that declares ``env_file: .env``) must pair the write
   with ``chmod 600`` (and ``umask 077`` where a shell snippet creates or
   appends secrets).

These tests are fail-closed: they fail on the pre-fix content and pass once
the policy headers, pins, and permission hardening land.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

# Every in-repo file that shows an operator how to create/append a `.env`
# holding Postgres/Valkey/S3/restic secrets. Each must document 0600.
ENV_WRITE_PATHS = [
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / ".env.example",
    REPO_ROOT / "docs/deployment/getting-started.md",
    REPO_ROOT / "docs/deployment/docker/quick-start.md",
    REPO_ROOT / "docs/deployment/docker/configuration.md",
    REPO_ROOT / "src/api/routes/docs/router/env_generator.py",
]

# Shell-doc files whose snippets create/append secrets via redirection; those
# snippets must additionally run under `umask 077`.
UMASK_SCOPE = [
    REPO_ROOT / "docs/deployment/getting-started.md",
    REPO_ROOT / "docs/deployment/docker/quick-start.md",
    REPO_ROOT / "docs/deployment/docker/configuration.md",
    REPO_ROOT / ".env.example",
]


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file to exist: {path}"
    return path.read_text(encoding="utf-8")


def test_no_dev_branch_fetch_without_optin() -> None:
    """Compose must gate any dev-branch fetch behind ALLOW_DEV_COMPOSE=1."""
    text = _read(COMPOSE_PATH)
    # No unguarded raw dev-branch compose URL may appear in-repo.
    for match in re.finditer(r"(?i)\bdev\b[^.\n]*docker-compose\.yml", text):
        context = text[max(0, match.start() - 200) : match.end() + 200]
        assert "ALLOW_DEV_COMPOSE" in context, (
            "dev-branch compose reference without ALLOW_DEV_COMPOSE opt-in: "
            + match.group(0)
        )
    # The opt-in gate itself must be documented fail-closed ...
    assert "ALLOW_DEV_COMPOSE" in text, (
        "docker-compose.yml must document the ALLOW_DEV_COMPOSE=1 opt-in gate "
        "for unreleased dev-branch compose (fail-closed by default)"
    )
    # ... together with checksum/digest verification for the fetched ref.
    assert re.search(r"(?i)sha256|digest|checksum", text), (
        "docker-compose.yml must point at checksum/digest verification for "
        "any fetched compose ref"
    )


def test_no_silent_registry_fallback() -> None:
    """No silent docker.io fallback; no floating :latest third-party images."""
    text = _read(COMPOSE_PATH)
    # A silent cross-trust-domain fallback is forbidden: any Docker Hub
    # fallback must be behind the explicit opt-in.
    for match in re.finditer(r"docker\.io/", text):
        context = text[max(0, match.start() - 400) : match.end() + 400]
        assert "ALLOW_DOCKERHUB_FALLBACK" in context, (
            "docker.io reference without ALLOW_DOCKERHUB_FALLBACK opt-in: "
            + match.group(0)
        )
    assert "ALLOW_DOCKERHUB_FALLBACK" in text, (
        "docker-compose.yml must document that Docker Hub fallback requires "
        "explicit ALLOW_DOCKERHUB_FALLBACK=1 opt-in (fail-closed by default)"
    )
    # Third-party images must be pinned to a stable tag/digest, never bare
    # `:latest` (operator-controlled BACKEND_VERSION/CLIENT_VERSION defaults
    # are version variables, not floating third-party pins).
    for match in re.finditer(r"(?m)^\s*image:\s*(\S+)\s*$", text):
        image = match.group(1)
        if "${" in image:
            continue  # operator-versioned first-party image, not a float
        assert not image.endswith(":latest"), (
            f"floating third-party image tag must be pinned: {image}"
        )


def test_env_write_paths_use_chmod_600() -> None:
    """Every in-repo .env write path must pair the write with chmod 600."""
    for path in ENV_WRITE_PATHS:
        text = _read(path)
        assert "chmod 600" in text or "0o600" in text, (
            f"{path.relative_to(REPO_ROOT)} writes/declares .env secrets but "
            "never sets 0600 permissions"
        )


def test_env_shell_snippets_use_umask_077() -> None:
    """Shell snippets that create/append .env secrets must set umask 077."""
    for path in UMASK_SCOPE:
        text = _read(path)
        writes_env = bool(
            re.search(
                r"(?m)(>>?[^|\n]*\.env\b|cp\s+\S*\.env\.example\s+\S*\.env\b)", text
            )
        )
        assert writes_env, (
            f"{path.relative_to(REPO_ROOT)} no longer shows a .env write; "
            "update this test's scope instead of silently dropping coverage"
        )
        assert "umask 077" in text, (
            f"{path.relative_to(REPO_ROOT)} shows a .env write without "
            "`umask 077` guarding the creation"
        )
