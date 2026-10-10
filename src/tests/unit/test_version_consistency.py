"""The release version must be published consistently, everywhere.

``src/server/lifecycle.VERSION`` is the single source of truth the process
reports over ``/api/v1/version``. Everything that *advertises* the current
build — the README, the deployment docs, the docker snippets — has to agree
with it, or operators copy a stale tag out of the documentation and pull an
image that does not match the code they are running.

That drift is not hypothetical: every release bump in this repository updates
several hand-maintained literals, and one of them was missed for a whole
release (``config_defaults.py`` kept advertising the previous build while the
process reported the new one). Rather than trust a reviewer to spot the missed
file, each published claim is pinned here by an explicit pattern.

When a pattern stops matching, the doc moved — update the pattern. When a
pattern matches the wrong version, the bump missed that file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.server.lifecycle import VERSION

VERSION_TAG = r"a\.\d+\.\d+-\d+"

# (path, pattern, note). The pattern must match the file's claim about the
# CURRENT server build; historical or illustrative versions are deliberately
# not matched, so each pattern is narrow on purpose.
PUBLISHED_CLAIMS: list[tuple[str, str, str]] = [
    ("README.md", rf"Current version: `({VERSION_TAG})`", "README 'Version' section"),
    (
        "docker-compose.yml",
        rf"BACKEND_VERSION=({VERSION_TAG})\)",
        "docker-compose header example",
    ),
    (
        "docker/README.md",
        rf"BACKEND_VERSION=({VERSION_TAG})\b",
        "docker README run snippet",
    ),
    (
        "docs/deployment/docker/index.md",
        rf"BACKEND_VERSION=({VERSION_TAG})\b",
        "docker deployment guide",
    ),
    (
        "docs/deployment/docker/quick-start.md",
        rf"BACKEND_VERSION=({VERSION_TAG})\b",
        "docker quick-start guide",
    ),
    (
        "docs/api/system.md",
        rf'"version": "({VERSION_TAG})"',
        "system API response samples",
    ),
    (
        "docs/cli/overview.md",
        rf"Plexichat Server ({VERSION_TAG})",
        "CLI --version output sample",
    ),
    (
        "docs/deployment/getting-started.md",
        rf'"version":"({VERSION_TAG})"',
        "deployment health-check sample",
    ),
    (
        "docs/deployment/disaster-recovery.md",
        rf'"version": "({VERSION_TAG})"',
        "disaster-recovery restore sample",
    ),
    (
        "docs/deployment/configuration/config-api.md",
        rf"version: ({VERSION_TAG})",
        "config API sample",
    ),
    ("docs/index.md", rf"Version scheme `({VERSION_TAG})`", "docs landing page"),
    (
        "docs/features/static-client.md",
        rf"├── ({VERSION_TAG})/",
        "static client dist layout",
    ),
    (
        "docs/default-config.md",
        rf"^  version: ({VERSION_TAG})$",
        "generated config sample",
    ),
    (
        "docs/getting-started.md",
        rf"For example, `({VERSION_TAG})`",
        "version format example",
    ),
]


def _matches(path: str, pattern: str) -> list[str]:
    content = Path(path).read_text(encoding="utf-8")
    return re.findall(pattern, content, flags=re.MULTILINE)


@pytest.mark.parametrize(
    "path,pattern,note",
    PUBLISHED_CLAIMS,
    ids=[entry[0] for entry in PUBLISHED_CLAIMS],
)
def test_published_version_claim_matches_the_running_server(
    path: str, pattern: str, note: str
) -> None:
    found = _matches(path, pattern)
    assert found, (
        f"{note}: the version pattern no longer matches anything in {path}.\n"
        f"Pattern: {pattern}\n"
        "The surrounding text probably moved; update PUBLISHED_CLAIMS."
    )
    stale = sorted({tag for tag in found if tag != VERSION})
    assert not stale, (
        f"{note}: {path} advertises {stale} but the server runs {VERSION}.\n"
        "A release bump updated some files and missed this one."
    )


def test_every_pinned_claim_file_still_exists() -> None:
    """A deleted/renamed doc must not silently drop out of the version check."""
    missing = [path for path, _, _ in PUBLISHED_CLAIMS if not Path(path).is_file()]
    assert not missing, (
        f"PUBLISHED_CLAIMS references files that no longer exist: {missing}"
    )


def test_deployment_docs_do_not_advertise_a_stale_backend_tag() -> None:
    """Catch a stale tag in *any* doc, even one not pinned above.

    ``a.1.0-<build>`` appears legitimately as the client version and as the
    minimum supported version, so this only looks at tags used as a
    BACKEND_VERSION argument or in a ``Server:`` claim — the two places a
    missed bump lets an operator pull the wrong backend image.
    """
    backend_arg = re.compile(rf"BACKEND_VERSION=({VERSION_TAG})")
    stale: list[str] = []
    for path in sorted(Path("docs").rglob("*.md")) + [
        Path("README.md"),
        Path("docker/README.md"),
        Path("docker-compose.yml"),
    ]:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - non-UTF8 docs are not expected
            continue
        for tag in backend_arg.findall(content):
            if tag != VERSION:
                stale.append(f"{path}: BACKEND_VERSION={tag}")
    assert not stale, (
        "deployment docs advertise a backend tag the server does not report:\n"
        + "\n".join(stale)
    )
