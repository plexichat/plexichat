"""Tests for ``ci-tooling/install_scanners.py``, and for how CI calls it.

The scanners this installs are the pipeline's only check that a *published*
dependency advisory is not sitting in the pinned tree, so the contract is:

1. Pins -- every tool is fetched from a versioned release URL with a full
   sha256. The osv-scanner step used to fetch ``releases/latest``, which is how
   it rotted unnoticed when upstream dropped the ``.tar.gz`` asset.
2. Verification -- nothing is installed unless the bytes match that digest, and
   a mismatch leaves no runnable binary behind.
3. Extraction -- an archive member is taken by name, so a hostile tarball
   cannot write outside the install directory.
4. The CI callers -- both configs run the scans without ``|| true``, and trivy
   is given ``--exit-code 1`` (its default is 0 even when it finds something),
   because a scan whose status is discarded is not a check.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import re
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "install_scanners.py"
GITLAB_CI = REPO_ROOT / ".gitlab-ci.yml"
GITHUB_PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"


@pytest.fixture(scope="module")
def scanners():
    """The installer module, loaded by path (``ci-tooling`` is not a package)."""
    spec = importlib.util.spec_from_file_location("install_scanners", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: bytes) -> str:
    path.write_bytes(payload)
    return path.as_uri()


def test_every_pin_is_versioned_and_has_a_full_sha256(scanners):
    """A moving URL or a short digest would silently weaken the install."""
    for name, spec in scanners.TOOLS.items():
        assert re.fullmatch(r"v\d+\.\d+\.\d+", spec["version"]), name
        assert re.fullmatch(r"[0-9a-f]{64}", spec["sha256"]), name
        url = scanners.tool_url(name)
        assert spec["version"] in url, name
        assert "latest" not in url, f"{name} must not track a moving release"
        assert url.startswith("https://github.com/"), name


def test_a_pinned_release_is_installed_executable(scanners, tmp_path, monkeypatch):
    payload = b"#!/bin/sh\necho scanner\n"
    uri = _write(tmp_path / "osv-scanner_linux_amd64", payload)
    monkeypatch.setitem(scanners.TOOLS["osv-scanner"], "sha256", _digest(payload))

    installed = scanners.install_tool("osv-scanner", str(tmp_path / "bin"), url=uri)

    assert Path(installed) == tmp_path / "bin" / "osv-scanner"
    assert Path(installed).read_bytes() == payload
    assert os.access(installed, os.X_OK)


def test_a_digest_mismatch_installs_nothing(scanners, tmp_path):
    """The bytes that arrive are not the bytes that were asked for."""
    uri = _write(tmp_path / "osv-scanner_linux_amd64", b"not what was pinned")
    bin_dir = tmp_path / "bin"

    with pytest.raises(scanners.InstallError) as caught:
        scanners.install_tool("osv-scanner", str(bin_dir), url=uri)

    assert "digest mismatch" in str(caught.value)
    assert not (bin_dir / "osv-scanner").exists(), "a rejected download must not land"
    assert list(bin_dir.iterdir()) == [], "nor may it leave its scratch directory"


def test_an_archive_member_is_taken_by_name(scanners, tmp_path, monkeypatch):
    """Only the named binary is extracted, whatever else the tarball holds."""
    archive_path = tmp_path / "trivy.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        binary = tarfile.TarInfo("trivy")
        body = b"#!/bin/sh\necho trivy\n"
        binary.size = len(body)
        archive.addfile(binary, io.BytesIO(body))

        # A tarball that also tries to escape the install directory.
        escape = tarfile.TarInfo("../escape")
        escape.size = 4
        archive.addfile(escape, io.BytesIO(b"pwnd"))

    monkeypatch.setitem(
        scanners.TOOLS["trivy"], "sha256", _digest(archive_path.read_bytes())
    )
    bin_dir = tmp_path / "bin"

    installed = scanners.install_tool("trivy", str(bin_dir), url=archive_path.as_uri())

    assert Path(installed).read_bytes() == b"#!/bin/sh\necho trivy\n"
    assert not (tmp_path / "escape").exists()
    assert sorted(p.name for p in bin_dir.iterdir()) == ["trivy"]


def test_an_archive_without_its_binary_is_an_error(scanners, tmp_path, monkeypatch):
    archive_path = tmp_path / "trivy.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        other = tarfile.TarInfo("README.md")
        other.size = 3
        archive.addfile(other, io.BytesIO(b"hi\n"))
    monkeypatch.setitem(
        scanners.TOOLS["trivy"], "sha256", _digest(archive_path.read_bytes())
    )

    with pytest.raises(scanners.InstallError) as caught:
        scanners.install_tool("trivy", str(tmp_path / "bin"), url=archive_path.as_uri())

    assert "does not contain" in str(caught.value)


def test_a_transport_other_than_https_is_refused(scanners, tmp_path, monkeypatch):
    """Only https, or a file:// fixture that exists, may be fetched."""
    payload = b"binary"
    monkeypatch.setitem(scanners.TOOLS["osv-scanner"], "sha256", _digest(payload))

    for url in (
        "http://example.invalid/osv-scanner_linux_amd64",
        "ftp://example.invalid/osv-scanner_linux_amd64",
        "/tmp/osv-scanner_linux_amd64",
    ):
        with pytest.raises(scanners.InstallError):
            scanners.install_tool("osv-scanner", str(tmp_path / "bin"), url=url)

    missing = (tmp_path / "absent").as_uri()
    with pytest.raises(scanners.InstallError) as caught:
        scanners.install_tool("osv-scanner", str(tmp_path / "bin"), url=missing)
    assert "names no file" in str(caught.value)


def test_the_installed_binary_is_not_world_executable(scanners, tmp_path, monkeypatch):
    payload = b"binary"
    uri = _write(tmp_path / "osv-scanner_linux_amd64", payload)
    monkeypatch.setitem(scanners.TOOLS["osv-scanner"], "sha256", _digest(payload))

    installed = scanners.install_tool("osv-scanner", str(tmp_path / "bin"), url=uri)

    assert oct(os.stat(installed).st_mode & 0o777) == "0o700"


def test_an_unknown_tool_is_a_usage_error(scanners, tmp_path):
    with pytest.raises(scanners.InstallError):
        scanners.install_tool("curl", str(tmp_path / "bin"))

    with pytest.raises(SystemExit) as caught:
        scanners.main(["curl"])
    assert caught.value.code == 2


def _executable_lines(text: str) -> list[str]:
    """The non-comment lines of a CI config.

    Comments in these files name the broken spelling they replaced, so a guard
    that reads every line would trip over its own explanation -- the same trap
    the landing suite's ``${var,,}`` guard documents.
    """
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_the_scan_step_fails_the_job_in_both_ci_systems():
    """The regression guard: a scan whose status is thrown away is not a check.

    Both configs once installed osv-scanner with ``|| true`` and ran
    ``./bin/osv-scanner . || true`` against a binary the 404 never produced, and
    trivy's own exit code is 0 by default, so the "dependency scanning" job was
    green whether or not anything was scanned.
    """
    gitlab = GITLAB_CI.read_text(encoding="utf-8")
    github = GITHUB_PR_CHECKS.read_text(encoding="utf-8")

    assert "ci-tooling/install_scanners.py" in gitlab
    assert "ci-tooling/install_scanners.py" in github

    for label, text in (("GitLab", gitlab), ("GitHub", github)):
        lines = _executable_lines(text)
        assert not [
            line for line in lines if "osv-scanner_linux_amd64.tar.gz" in line
        ], f"{label}: the v1 asset is a 404"

        for line in lines:
            if "./bin/osv-scanner" not in line and "./bin/trivy" not in line:
                continue
            if "--version" in line:
                continue
            # Any ``||`` fallback discards the finding the scan just made.
            assert "||" not in line, f"{label}: scan status is discarded: {line}"
        for line in lines:
            if "./bin/trivy" in line and "--version" not in line:
                assert "--exit-code 1" in line, (
                    f"{label}: trivy exits 0 whatever it finds: {line}"
                )

    # A report that was never written must not upload as a green no-op.
    assert "if-no-files-found: error" in github
