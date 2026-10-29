"""Unit tests for ``ci-tooling/check_self_test_license.py``.

The helper decides, before a self-test job starts, whether the licence the
server would actually load grants PlexiJoin. Pipeline 2234 is the regression
these tests pin: ``$PLEXICHAT_LICENSE`` was set but its signature did not
verify, so the licensing module loaded it (the env var outranks the on-disk
licence), validation failed, the server ran free tier, and every gated route
answered 403 -- which the suite records as an EXPECTED DENIAL. The licensed
PostgreSQL paths therefore never executed and CI stayed green.

The check runs as a subprocess rather than imported: the licensing module is a
process-global singleton (``setup()`` rewrites its manager and free-tier flag),
so importing it here would leak licence state into every other test that reads
it. A subprocess also exercises the real CLI contract -- the exit status the
CI job branches on -- instead of a re-implementation of it.
"""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CHECK_SCRIPT = _PROJECT_ROOT / "ci-tooling" / "check_self_test_license.py"
_WRITE_SCRIPT = _PROJECT_ROOT / "ci-tooling" / "write_test_license.py"

DEFAULT_LICENSE_NAME = Path(".plexichat") / "config" / "license"


def _run(home: Path, license_value: str = None) -> subprocess.CompletedProcess:
    """Run the check with a hermetic HOME and ``PLEXICHAT_LICENSE`` value."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PLEXICHAT_LICENSE", "HOME", "USERPROFILE")
    }
    env["HOME"] = str(home)
    if license_value is not None:
        env["PLEXICHAT_LICENSE"] = license_value
    return subprocess.run(
        [sys.executable, str(_CHECK_SCRIPT)],
        cwd=str(_PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _provisional_license_file(tmp_path: Path) -> Path:
    """Write the canonical provisional test licence via the CI helper."""
    target = tmp_path / "provisional-license.json"
    result = subprocess.run(
        [sys.executable, str(_WRITE_SCRIPT), "--path", str(target)],
        cwd=str(_PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return target


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def test_no_license_is_unusable(tmp_path):
    result = _run(tmp_path)

    assert result.returncode == 1
    assert "no valid licence loaded" in result.stderr


def test_provisional_test_license_grants_plexijoin(tmp_path):
    license_file = _provisional_license_file(tmp_path)

    result = _run(tmp_path, _b64(license_file))

    assert result.returncode == 0, result.stderr
    assert "grants PlexiJoin" in result.stdout


def test_env_license_as_file_path_grants_plexijoin(tmp_path):
    license_file = _provisional_license_file(tmp_path)

    result = _run(tmp_path, str(license_file))

    assert result.returncode == 0, result.stderr
    assert "grants PlexiJoin" in result.stdout


def test_default_location_is_used_when_env_is_unset(tmp_path):
    license_file = _provisional_license_file(tmp_path)
    default = tmp_path / DEFAULT_LICENSE_NAME
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text(license_file.read_text(encoding="utf-8"), encoding="utf-8")

    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "grants PlexiJoin" in result.stdout


def test_non_base64_env_value_is_unusable(tmp_path):
    result = _run(tmp_path, "not-a-license")

    assert result.returncode == 1
    assert "unusable for the licensed endpoints" in result.stderr


def test_invalid_env_license_is_not_rescued_by_a_valid_file(tmp_path):
    """The pipeline-2234 shape: a signed-but-broken env value still wins.

    ``licensing.setup()`` consults the env var before the default location, so
    a value that parses but fails signature verification leaves the server in
    free tier even when a perfectly good licence sits on disk. The check has to
    report that (exit 1) so the job falls back instead of trusting the variable.
    """
    good_file = _provisional_license_file(tmp_path)
    default = tmp_path / DEFAULT_LICENSE_NAME
    default.parent.mkdir(parents=True, exist_ok=True)
    default.write_text(good_file.read_text(encoding="utf-8"), encoding="utf-8")

    broken = json.loads(good_file.read_text(encoding="utf-8"))
    broken["type"] = "commercial"
    broken["signature"] = base64.b64encode(b"x" * 64).decode()
    broken_value = base64.b64encode(json.dumps(broken).encode()).decode()

    result = _run(tmp_path, broken_value)

    assert result.returncode == 1, result.stdout
    assert "unusable for the licensed endpoints" in result.stderr


@pytest.mark.parametrize("exit_status", [0, 1])
def test_exit_status_is_the_only_signal_the_job_needs(tmp_path, exit_status):
    """Guard the contract the CI script branches on, in both directions."""
    license_file = _provisional_license_file(tmp_path)
    value = _b64(license_file) if exit_status == 0 else "garbage"

    assert _run(tmp_path, value).returncode == exit_status
