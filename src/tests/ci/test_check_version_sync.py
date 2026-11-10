"""Regression tests for ci-tooling/check_version_sync.py's ``--fix`` rewrites.

The fixer matched every ``VERSION="..."`` assignment, including the resolver
line ``VERSION="$(normalize_version "$VERSION")"``. Rewriting that produced
``VERSION="1.0.0a215"$VERSION")"``: deploy.sh then published a version string
with the resolver's tail glued onto it, which the landing deploy-flag suite
caught as eleven failures plus two stale checksum sidecars.

These tests pin the shapes the fixer must leave alone, and the assertion it is
supposed to update, so the same corruption cannot come back silently.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "check_version_sync", REPO_ROOT / "ci-tooling" / "check_version_sync.py"
)
assert _SPEC is not None and _SPEC.loader is not None
# ``dataclass`` looks its module up in ``sys.modules`` while building the
# class, so register it before executing: an unregistered module makes
# ``@dataclass`` fail with "'NoneType' object has no attribute '__dict__'".
check_version_sync = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = check_version_sync
_SPEC.loader.exec_module(check_version_sync)

BACKEND = "2.0.0a7"
CLIENT = "2.0.0a3"

SH_SOURCE = (
    'VERSION="1.0.0a1"\n'
    'BACKEND_VERSION="1.0.0a1"\n'
    'CLIENT_VERSION="1.0.0a2"\n'
    'VERSION="$(normalize_version "$VERSION")"\n'
    'BACKEND_VERSION="$(normalize_version "$BACKEND_VERSION")"\n'
    'CLIENT_VERSION="$(normalize_version "$CLIENT_VERSION")"\n'
)

PS1_SOURCE = (
    '[string]$Version = "1.0.0a1"\n'
    '[string]$BackendVersion = "1.0.0a1"\n'
    '[string]$ClientVersion = "1.0.0a2"\n'
    "$Version = Normalize-Version -v $Version\n"
    "$BackendVersion = Normalize-Version -v $BackendVersion\n"
)


def _landing(tmp_path: Path) -> Path:
    landing = tmp_path / "plexichat-landing"
    (landing / "tests").mkdir(parents=True)
    return landing


def _apply(landing: Path, func, filename: str) -> Tuple[str, List[object]]:
    issues: List[object] = []
    fixes: List[object] = []
    func(landing.parent, BACKEND, CLIENT, issues, fixes)
    for fix in fixes:
        fix.path.write_text(fix.updated, encoding="utf-8")
    return (landing / filename).read_text(encoding="utf-8"), issues


def test_deploy_sh_defaults_change_but_resolver_lines_do_not(tmp_path: Path) -> None:
    landing = _landing(tmp_path)
    (landing / "deploy.sh").write_text(SH_SOURCE, encoding="utf-8")

    text, issues = _apply(landing, check_version_sync.check_deploy_sh, "deploy.sh")

    assert f'VERSION="{BACKEND}"' in text
    assert f'BACKEND_VERSION="{BACKEND}"' in text
    assert f'CLIENT_VERSION="{CLIENT}"' in text
    # The resolver calls are code and must survive byte for byte.
    assert 'VERSION="$(normalize_version "$VERSION")"' in text
    assert 'BACKEND_VERSION="$(normalize_version "$BACKEND_VERSION")"' in text
    assert 'CLIENT_VERSION="$(normalize_version "$CLIENT_VERSION")"' in text
    # The corruption this test exists for: a version prefix glued onto the call.
    assert f'"{BACKEND}"$VERSION' not in text
    assert f'"{BACKEND}"$BACKEND_VERSION' not in text
    assert not [issue for issue in issues if issue.level == "FAIL"]


def test_deploy_ps1_defaults_change_but_calls_do_not(tmp_path: Path) -> None:
    landing = _landing(tmp_path)
    (landing / "deploy.ps1").write_text(PS1_SOURCE, encoding="utf-8")

    text, issues = _apply(landing, check_version_sync.check_deploy_ps1, "deploy.ps1")

    assert f'[string]$Version = "{BACKEND}"' in text
    assert f'[string]$BackendVersion = "{BACKEND}"' in text
    assert f'[string]$ClientVersion = "{CLIENT}"' in text
    assert "$Version = Normalize-Version -v $Version" in text
    assert "$BackendVersion = Normalize-Version -v $BackendVersion" in text
    assert not [issue for issue in issues if issue.level == "FAIL"]


def test_published_pair_assertion_is_synced_in_the_deploy_flags_suite(
    tmp_path: Path,
) -> None:
    landing = _landing(tmp_path)
    (landing / "deploy.sh").write_text(
        f'VERSION="{BACKEND}"\n'
        f'BACKEND_VERSION="{BACKEND}"\n'
        f'CLIENT_VERSION="{CLIENT}"\n',
        encoding="utf-8",
    )
    suite = landing / "tests" / "test_deploy_flags.sh"
    suite.write_text(
        'check "defaults resolve to the published Server/Client pair" \\\n'
        '    "VERSION=1.0.0a1 BACKEND_VERSION=1.0.0a1 CLIENT_VERSION=1.0.0a2"\n',
        encoding="utf-8",
    )

    issues: List[object] = []
    fixes: List[object] = []
    check_version_sync.check_deploy_sh(landing.parent, BACKEND, CLIENT, issues, fixes)
    for fix in fixes:
        fix.path.write_text(fix.updated, encoding="utf-8")

    updated = suite.read_text(encoding="utf-8")
    assert (
        f'"VERSION={BACKEND} BACKEND_VERSION={BACKEND} CLIENT_VERSION={CLIENT}"'
        in updated
    )
    assert not [issue for issue in issues if "assert" in issue.message]


def test_assertion_that_derives_defaults_is_left_alone(tmp_path: Path) -> None:
    landing = _landing(tmp_path)
    (landing / "deploy.sh").write_text(
        f'VERSION="{BACKEND}"\n'
        f'BACKEND_VERSION="{BACKEND}"\n'
        f'CLIENT_VERSION="{CLIENT}"\n',
        encoding="utf-8",
    )
    suite = landing / "tests" / "test_deploy_flags.sh"
    derived = (
        'check "defaults resolve to the published Server/Client pair" \\\n'
        '    "VERSION=$DEFAULT_BACKEND BACKEND_VERSION=$DEFAULT_BACKEND "\n'
        '    "CLIENT_VERSION=$DEFAULT_CLIENT"\n'
    )
    suite.write_text(derived, encoding="utf-8")

    issues: List[object] = []
    fixes: List[object] = []
    check_version_sync.check_deploy_sh(landing.parent, BACKEND, CLIENT, issues, fixes)

    assert suite.read_text(encoding="utf-8") == derived
    assert not [issue for issue in issues if issue.level == "FAIL"]


def test_fix_is_idempotent(tmp_path: Path) -> None:
    landing = _landing(tmp_path)
    (landing / "deploy.sh").write_text(SH_SOURCE, encoding="utf-8")

    first, _ = _apply(landing, check_version_sync.check_deploy_sh, "deploy.sh")
    second, _ = _apply(landing, check_version_sync.check_deploy_sh, "deploy.sh")

    assert second == first
