"""Static hardening invariants for the backend/backup/janus container build.

These tests read the Dockerfiles, ``.dockerignore`` and compose files as text
and fail closed on the pre-hardening content. They are deliberately cheap (no
daemon, no build) so they can run in every CI job and catch a regression the
moment someone drops a needed flag or reintroduces a secret into the context.

Covered:

1. Backend image runs as UID 10001 (non-root) with correct WORKDIR/volume
   ownership.
2. Every ``pip install`` in the backend image is ``--require-hashes`` (and
   ``--no-cache-dir``).
3. No ``ADD`` and no ``COPY``/``ENV`` of secret material into any image.
4. ``HEALTHCHECK`` in the backend Dockerfile and the backup healthcheck in
   compose are present.
5. ``.dockerignore`` excludes VCS metadata, virtualenvs, .env/secrets and tests.
6. Base images are version-pinned and the libbrotli1 CVE fix plus base-image
   security upgrades are applied.
7. Backup/Janus third-party images and the supercronic build are pinned.

Note on the backup image: it intentionally has no ``USER`` directive. Its
entrypoint writes ``/etc/crontab`` and the named volumes it populates
(``/repo``, ``/plexichat-home``, ``/rustfs-data``) are host-owned, so dropping
to an unprivileged UID at build time would break it. Non-root is therefore
enforced only for the internet-facing backend image.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
COMPOSE = REPO_ROOT / "docker-compose.yml"
BACKUP_DOCKERFILE = REPO_ROOT / "docker/backup/Dockerfile"
HEALTHCHECK_SCRIPT = REPO_ROOT / "docker/backup/scripts/healthcheck.sh"
JANUS_COMPOSE = REPO_ROOT / "docker/janus/docker-compose.janus.yml"


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file to exist: {path}"
    return path.read_text(encoding="utf-8")


def test_backend_runs_as_non_root_with_owned_paths() -> None:
    """The FastAPI process must run as the dedicated UID 10001 account."""
    text = _read(DOCKERFILE)
    assert re.search(r"(?m)^USER\s+plexichat\s*$", text) or re.search(
        r"(?m)^USER\s+10001(?::10001)?\s*$", text
    ), "backend Dockerfile must end with a non-root USER directive"
    assert "useradd" in text and "10001" in text, (
        "backend Dockerfile must create the UID 10001 service account"
    )
    assert re.search(r"COPY\s+--chown=10001:10001\s", text), (
        "WORKDIR /app content must be chowned to the non-root user via COPY "
        "--chown (a trailing chown -R duplicates the whole tree in a layer)"
    )
    assert re.search(r"mkdir -p\s+/home/plexichat/\.plexichat", text), (
        "the .plexichat volume mountpoint must be pre-created and owned by the "
        "unprivileged user so first-time named-volume population inherits it"
    )
    assert not re.search(r"(?m)^USER\s+root\s*$", text), (
        "backend Dockerfile must not leave a USER root directive in the image"
    )


def test_pip_install_is_hash_pinned() -> None:
    """No pip install may run without a hash-checked, cache-free requirements set."""
    text = _read(DOCKERFILE)
    installs = list(re.finditer(r"(?m)^RUN\s+.*pip\s+install\b.*$", text))
    assert installs, "expected a pip install step in the backend Dockerfile"
    for match in installs:
        line = match.group(0)
        assert "--require-hashes" in line, (
            "pip install must use --require-hashes: " + line
        )
        assert "--no-cache-dir" in line, (
            "pip install must use --no-cache-dir to keep wheels out of layers: " + line
        )


def test_no_secrets_or_add_baked_into_images() -> None:
    """No image may use ADD, COPY a secret file, or bake a secret into ENV."""
    for path in (DOCKERFILE, BACKUP_DOCKERFILE):
        text = _read(path)
        assert not re.search(r"(?mi)^\s*ADD\s+", text), (
            f"{path.name} must use COPY, not ADD (ADD fetches remote URLs)"
        )
        for match in re.finditer(r"(?mi)^\s*COPY\b.*$", text):
            line = match.group(0)
            assert not re.search(
                r"\.env\b|\.pem\b|\.key\b|\.crt\b|id_rsa|credentials", line, re.I
            ), f"{path.name} copies secret material: {line}"
        for match in re.finditer(
            r"(?mi)^\s*ENV\s+([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|APIKEY|PRIVATE_KEY)[A-Z0-9_]*)=(\S+)",
            text,
        ):
            assert False, f"{path.name} bakes a secret into ENV: {match.group(1)}"


def test_healthchecks_present() -> None:
    """Backend and backup services must both define a liveness check."""
    assert re.search(r"(?m)^HEALTHCHECK\b", _read(DOCKERFILE)), (
        "backend Dockerfile must define a HEALTHCHECK"
    )
    compose = _read(COMPOSE)
    assert "healthcheck:" in compose, "docker-compose.yml needs healthchecks"
    assert "/scripts/healthcheck.sh" in compose, (
        "backup service healthcheck must call the marker-aware healthcheck.sh"
    )
    healthcheck = _read(HEALTHCHECK_SCRIPT)
    assert "pgrep" in healthcheck, (
        "backup healthcheck must confirm the cron loop is actually alive"
    )


def test_dockerignore_excludes_secrets_vcs_and_tests() -> None:
    """The build context must not carry .git, .venv, .env, secrets or tests."""
    entries = {
        line.strip()
        for line in _read(DOCKERIGNORE).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for required in (".git", ".venv", ".env", ".secrets.baseline", "src/tests"):
        assert required in entries, f".dockerignore must exclude {required}"
    assert ".env.*" in entries and "**/.env" in entries, (
        ".dockerignore must exclude .env variants at any depth"
    )
    assert any(p in entries for p in ("*.pem", "**/*.pem")), (
        ".dockerignore must exclude private key/cert material"
    )
    assert any(t in entries for t in ("test_*.py", "**/test_*.py")), (
        ".dockerignore must exclude test modules from the runtime image"
    )


def test_base_images_version_pinned_and_security_updates_applied() -> None:
    """Pinned distro/series plus the brotli CVE fix and base security upgrades."""
    text = _read(DOCKERFILE)
    from_lines = re.findall(r"(?mi)^FROM\s+(\S+)", text)
    assert from_lines, "backend Dockerfile must declare a FROM image"
    for image in from_lines:
        assert not image.endswith(":latest"), f"floating base image: {image}"
    assert re.search(r"(?m)^FROM\s+python:3\.14\.\d+-slim-trixie\s*$", text), (
        "backend base must be a pinned python 3.14 patch release on trixie"
    )
    # The libbrotli1 fix and the base-image security refresh must stay.
    assert "libbrotli1" in text, (
        "libbrotli1 must be installed by name so apt selects the patched "
        "trixie-security candidate"
    )
    assert "CVE-2026-111497" in text and "deb13u1" in text, (
        "the libbrotli1 CVE rationale/pin must remain documented"
    )
    assert re.search(r"apt-get\s+upgrade\s+-y", text), (
        "apt-get upgrade -y must refresh already-installed base packages that "
        "receive trixie-security fixes (apt, ca-certificates, liblzma5, zlib1g)"
    )


def test_backend_strips_build_toolchain_but_keeps_runtime_libs() -> None:
    """The compiler/C-header layer must not survive into the shipped image.

    ``pip install`` needs gcc, git and the ``*-dev`` headers to build the
    wheels, but those tools are pure build-time weight (and extra attack
    surface) in a server image. They are purged with ``--auto-remove`` in the
    same layer as the install. Because apt does not know that the compiled
    extensions still link against the runtime .so packages, those must be
    marked manual first or autoremove would delete libpq5/libopus0/... and
    break psycopg/av/aiortc at startup.
    """
    text = _read(DOCKERFILE)
    # Join Dockerfile line continuations so each RUN is one logical line.
    logical = re.sub(r"\\\n\s*", " ", text)
    runtime_libs = ("libpq5", "libopus0", "libsrtp2-1", "libvpx9")
    build_deps = (
        "build-essential",
        "git",
        "libpq-dev",
        "libopus-dev",
        "libsrtp2-dev",
        "libvpx-dev",
    )

    manual = re.search(r"&&\s*apt-mark\s+manual\s+([^\n]+)", logical)
    assert manual, "runtime shared libs must be marked manual before the purge"
    for lib in runtime_libs:
        assert re.search(rf"\b{lib}\b", manual.group(1)), (
            f"apt-mark manual is missing {lib}"
        )

    purge = re.search(r"&&\s*apt-get\s+purge\b[^\n]*", logical)
    assert purge, "build toolchain must be purged after pip install"
    purge_line = purge.group(0)
    assert "--auto-remove" in purge_line, (
        "the purge must use --auto-remove to drop orphaned build-only deps"
    )
    for dep in build_deps:
        assert re.search(rf"\b{re.escape(dep)}\b", purge_line), (
            f"purge is missing {dep}"
        )
    # Runtime binaries pulled in by name must survive the purge.
    for kept in ("ffmpeg", "curl", "libbrotli1"):
        assert not re.search(rf"\b{kept}\b", purge_line), (
            f"{kept} is a runtime dependency and must not be purged"
        )

    # Ordering: mark manual (install layer) -> pip install -> purge.
    install_at = logical.index("pip install")
    assert manual.start() < install_at < purge.start(), (
        "runtime libs must be protected before the install, then build deps purged"
    )


def test_backup_and_janus_third_party_pins() -> None:
    """Backup and Janus third-party images must stay on explicit released pins."""
    backup = _read(BACKUP_DOCKERFILE)
    assert re.search(r"(?m)^FROM\s+golang:1\.\d+-alpine\b", backup), (
        "supercronic builder base must be a version-pinned Go alpine image"
    )
    assert re.search(r"(?m)^FROM\s+alpine:3\.\d+\s*$", backup), (
        "backup runtime base must be a version-pinned Alpine release"
    )
    assert re.search(r"(?m)^ARG\s+SUPERCRONIC_VERSION=0\.2\.33\s*$", backup), (
        "supercronic must be built from a pinned module version"
    )
    janus = _read(JANUS_COMPOSE)
    assert re.search(r"image:\s*canyan/janus-gateway:0\.10\.7", janus), (
        "Janus dev gateway must use a pinned released tag"
    )
    assert ":latest" not in janus, "Janus compose must not float on :latest"
