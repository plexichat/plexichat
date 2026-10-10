"""Tests for ``ci-tooling/license_audit.py``.

The audit is the gate that stops a new copyleft or unidentified license from
reaching the runtime image, so its classification rules and the allowlist /
documentation pairing are worth pinning down:

1. Classification -- permissive licenses pass, copyleft and unidentifiable ones
   do not, and missing metadata is a failure rather than a pass.
2. Lock scoping -- the audit reads the runtime lock, and "dev only" means
   dev-lock-only, not "named anywhere in the dev lock" (the dev lock is a
   superset of the runtime lock, so that distinction is the whole point).
3. Documentation -- every reviewed exception must be explained in
   ``THIRD_PARTY_NOTICES.md``.
4. The real runtime tree passes, and a missing lock is a usage error.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "license_audit.py"
NOTICES_PATH = REPO_ROOT / "THIRD_PARTY_NOTICES.md"


def _load_tool():
    spec = importlib.util.spec_from_file_location("license_audit", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve the module namespace.
    sys.modules["license_audit"] = module
    spec.loader.exec_module(module)
    return module


la = _load_tool()


def test_permissive_licenses_pass():
    assert la.classify(["MIT"]) is None
    assert la.classify(["BSD-3-Clause", "Apache-2.0"]) is None
    assert la.classify(["MIT-CMU"]) is None
    assert la.classify(["Unlicense"]) is None
    # MPL-2.0 is file-level copyleft with no link obligation, so it is accepted
    # (certifi / pywebpush are unavoidable runtime dependencies).
    assert la.classify(["MPL-2.0"]) is None


def test_copyleft_licenses_fail_with_a_reason():
    assert "copyleft" in (la.classify(["GPL-3.0-only"]) or "")
    assert "copyleft" in (la.classify(["LGPL-3.0-only"]) or "")
    assert "copyleft" in (la.classify(["AGPL-3.0-only"]) or "")


def test_unknown_licenses_fail_closed():
    assert "unknown" in (la.classify(["Totally Made Up 9.9"]) or "")
    # No metadata at all must never be treated as permissive.
    assert la.classify([]) == "no license metadata"


def test_normalize_maps_legacy_spellings():
    assert la.normalize("Apache License, Version 2.0") == "Apache-2.0"
    assert la.normalize("Mozilla Public License 2.0 (MPL 2.0)") == "MPL-2.0"
    assert la.normalize("PSFL") == "PSF-2.0"
    assert la.normalize("BSD") == "BSD-3-Clause"
    assert la.normalize("GNU Library or Lesser General Public License (LGPL)") == "LGPL"


def test_runtime_lock_excludes_test_tooling():
    names = la.pinned_names(REPO_ROOT / "requirements.txt")
    assert "fastapi" in names
    assert "valkey-glide-sync" in names
    # Test frameworks are dev-only now; they must not re-enter the runtime lock.
    assert "pytest" not in names
    assert "hypothesis" not in names
    assert "markdown" not in names
    # Permissive replacements for the former copyleft dependencies.
    assert "pg8000" in names
    assert "reportlab" in names


def test_dev_only_names_subtracts_the_runtime_tree():
    dev_only = la.dev_only_names()
    assert "pytest" in dev_only
    assert "hypothesis" in dev_only
    # Present in both locks, so it ships and must not be written off as tooling.
    assert "fastapi" not in dev_only
    assert "pg8000" not in dev_only


def test_allowlisted_exceptions_are_documented():
    notices = NOTICES_PATH.read_text(encoding="utf-8")
    for name in la.ALLOWED_COPYLEFT:
        assert name in notices, f"{name} is allowlisted but missing from the notices"
    for name in la.ALLOWED_UNKNOWN:
        assert name in notices, f"{name} is allowlisted but missing from the notices"


def test_no_gpl_family_dependency_ships_in_the_runtime_tree():
    """Anti-vacuity guard: the runtime tree must stay free of GPL/LGPL code.

    ``ALLOWED_COPYLEFT`` is empty by design — fpdf2 became reportlab, odfpy
    became a hand-rolled ODT writer, and psycopg became pg8000. Pinning that
    here means re-adding a copyleft dependency has to be a deliberate change to
    both the allowlist and the notices.
    """
    assert la.ALLOWED_COPYLEFT == {}

    names = la.pinned_names(REPO_ROOT / "requirements.txt")
    for removed in (
        "fpdf2",
        "odfpy",
        "psycopg",
        "psycopg-binary",
        "psycopg-pool",
        "psycopg2",
        "psycopg2-binary",
    ):
        assert removed not in names, f"{removed} is copyleft and must not ship"


def test_runtime_tree_passes_the_audit():
    # Packages absent from this environment are skipped, so this is valid both in
    # a dev environment and in CI where only requirements.txt is installed.
    assert la.main([]) == 0


def test_missing_lock_is_a_usage_error(tmp_path):
    assert la.main(["--requirements", str(tmp_path / "nope.txt")]) == 2
