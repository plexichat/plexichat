"""Tests for ``ci-tooling/check_pip_licenses.py``.

The ``pip-licenses`` CI stage is the independent second license gate (the
first is ``license_audit.py``), so pin down its contract:

1. Policy -- permissive licenses pass, copyleft / unknown / missing license
   strings fail closed, and reviewed exceptions honour the shared allowlists.
2. Scoping -- only the pinned runtime lock is gated; installed-but-unpinned
   tooling is ignored, and pinned-but-absent distributions are skipped.
3. Usage -- an unreadable report or lock file is a usage error (exit 2),
   not a clean pass.
4. Reality -- a ``pip-licenses``-shaped report built from this environment's
   real metadata passes for the current runtime tree.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "check_pip_licenses.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_pip_licenses", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pip_licenses"] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


def _write_report(tmp_path, rows):
    path = tmp_path / "pip-licenses.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _write_requirements(tmp_path, names):
    path = tmp_path / "requirements.txt"
    path.write_text("".join(f"{name}==1.0\n" for name in names), encoding="utf-8")
    return path


def test_permissive_licenses_pass(tmp_path):
    report = _write_report(
        tmp_path,
        [
            {"Name": "alpha", "Version": "1.0", "License": "MIT"},
            {"Name": "bravo", "Version": "1.0", "License": "Apache-2.0"},
            {"Name": "charlie", "Version": "1.0", "License": "BSD-3-Clause"},
        ],
    )
    reqs = _write_requirements(tmp_path, ["alpha", "bravo", "charlie"])
    assert tool.check(report, reqs) == 0


def test_legacy_spellings_pass(tmp_path):
    report = _write_report(
        tmp_path,
        [
            {"Name": "alpha", "Version": "1.0", "License": "MIT License"},
            {"Name": "bravo", "Version": "1.0", "License": "Apache Software License"},
            {
                "Name": "charlie",
                "Version": "1.0",
                "License": "Python Software Foundation License",
            },
        ],
    )
    reqs = _write_requirements(tmp_path, ["alpha", "bravo", "charlie"])
    assert tool.check(report, reqs) == 0


def test_copyleft_fails(tmp_path):
    report = _write_report(
        tmp_path,
        [
            {"Name": "good", "Version": "1.0", "License": "MIT"},
            {
                "Name": "evil",
                "Version": "1.0",
                "License": "GNU General Public License v3 (GPLv3)",
            },
        ],
    )
    reqs = _write_requirements(tmp_path, ["good", "evil"])
    assert tool.check(report, reqs) == 1


def test_unknown_and_missing_licenses_fail_closed(tmp_path):
    for bad in ("UNKNOWN", "", "Totally Made Up 9.9"):
        report = _write_report(
            tmp_path, [{"Name": "mystery", "Version": "1.0", "License": bad}]
        )
        reqs = _write_requirements(tmp_path, ["mystery"])
        assert tool.check(report, reqs) == 1, f"{bad!r} must fail closed"


def test_unusable_string_falls_back_to_installed_metadata(tmp_path):
    # "fastapi" is installed with clean metadata: an empty pip-licenses
    # string is accepted via the local re-resolution, not failed.
    report = _write_report(
        tmp_path, [{"Name": "fastapi", "Version": "1.0", "License": "UNKNOWN"}]
    )
    reqs = _write_requirements(tmp_path, ["fastapi"])
    assert tool.check(report, reqs) == 0


def test_copyleft_string_is_never_masked_by_the_fallback(tmp_path):
    # Even though fastapi's installed metadata is clean, a copyleft
    # pip-licenses string must still fail.
    report = _write_report(
        tmp_path, [{"Name": "fastapi", "Version": "1.0", "License": "GPL-3.0-only"}]
    )
    reqs = _write_requirements(tmp_path, ["fastapi"])
    assert tool.check(report, reqs) == 1


def test_unpinned_tooling_is_out_of_scope(tmp_path):
    # pip-audit / safety / pip-licenses themselves are installed in CI but not
    # pinned in requirements.txt: even a scary license there must not fail
    # the gate, because it never ships.
    report = _write_report(
        tmp_path,
        [
            {"Name": "fastapi", "Version": "1.0", "License": "MIT"},
            {"Name": "pip-licenses", "Version": "1.0", "License": "MIT"},
        ],
    )
    reqs = _write_requirements(tmp_path, ["fastapi"])
    assert tool.check(report, reqs) == 0


def test_pinned_but_absent_is_skipped_not_failed(tmp_path, capsys):
    report = _write_report(
        tmp_path, [{"Name": "fastapi", "Version": "1.0", "License": "MIT"}]
    )
    reqs = _write_requirements(tmp_path, ["fastapi", "not-installed-here"])
    assert tool.check(report, reqs) == 0
    assert "skipped" in capsys.readouterr().out


def test_reviewed_exceptions_are_honoured(tmp_path, monkeypatch):
    report = _write_report(
        tmp_path, [{"Name": "odd", "Version": "1.0", "License": "UNKNOWN"}]
    )
    reqs = _write_requirements(tmp_path, ["odd"])
    assert tool.check(report, reqs) == 1
    monkeypatch.setitem(tool.la.ALLOWED_UNKNOWN, "odd", "test exception")
    try:
        assert tool.check(report, reqs) == 0
    finally:
        del tool.la.ALLOWED_UNKNOWN["odd"]


def test_missing_report_or_lock_is_a_usage_error(tmp_path):
    reqs = _write_requirements(tmp_path, ["fastapi"])
    assert tool.check(tmp_path / "nope.json", reqs) == 2
    report = _write_report(
        tmp_path, [{"Name": "fastapi", "Version": "1.0", "License": "MIT"}]
    )
    assert tool.check(report, tmp_path / "nope.txt") == 2


def test_malformed_report_is_a_usage_error(tmp_path):
    bad = tmp_path / "pip-licenses.json"
    bad.write_text("{not json", encoding="utf-8")
    reqs = _write_requirements(tmp_path, ["fastapi"])
    assert tool.check(bad, reqs) == 2


def _synthetic_pip_licenses_row(dist):
    """Approximate what ``pip-licenses`` reports from raw local metadata.

    ``pip-licenses`` consults ``License-Expression`` first, then the
    ``License`` field, then the trove classifiers, so mirror that order.
    """
    meta = dist.metadata
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return {
            "Name": meta.get("Name"),
            "Version": dist.version,
            "License": expression,
        }
    field = (meta.get("License") or "").strip()
    if field:
        return {
            "Name": meta.get("Name"),
            "Version": dist.version,
            "License": field,
        }
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            return {
                "Name": meta.get("Name"),
                "Version": dist.version,
                "License": classifier.split("::")[-1].strip(),
            }
    return {"Name": meta.get("Name"), "Version": dist.version, "License": "UNKNOWN"}


def _pinned_versions(lock_path):
    """``{canonical-name: version}`` for ``name==version`` pins in a lock."""
    import re

    pins = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        match = re.match(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)", line.split("#", 1)[0].strip()
        )
        if match:
            pins[tool.la.canonical(match.group(1))] = match.group(2)
    return pins


def test_real_runtime_tree_passes(tmp_path):
    """The current tree must pass the new gate just as it passes the old one.

    Builds a ``pip-licenses``-shaped report from this environment's real
    metadata for every pinned runtime distribution installed here at the
    pinned version.
    """
    pins = _pinned_versions(REPO_ROOT / "requirements.txt")
    rows = []
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if (
            name
            and tool.la.canonical(name) in pins
            and dist.version == pins[tool.la.canonical(name)]
        ):
            rows.append(_synthetic_pip_licenses_row(dist))
    assert rows, "expected some pinned runtime distributions to be installed"
    report = _write_report(tmp_path, rows)
    assert tool.check(report, REPO_ROOT / "requirements.txt") == 0
