#!/usr/bin/env python3
"""Fail CI when any pytest test exceeds a duration threshold.

Reads the JUnit XML report produced by ``pytest --junitxml=...`` (the report
pytest emits for ``test:pytest`` in ``.gitlab-ci.yml``) and exits non-zero if
any testcase took longer than ``--threshold`` seconds, listing every offender
with its measured duration so the pipeline log is actionable.

When ``--metrics-out`` is given, a per-job metrics JSON array is written next
to the gate verdict. ``scripts/trend_publish.py`` merges those snapshots into
a ``ci-metrics`` branch history and renders an HTML trend report, so per-job
test durations are tracked across pipelines and regressions show over time.

Usage::

    python scripts/check_slow_tests.py test-results.xml --threshold 5.0 \\
        --job test:pytest --metrics-out test-durations.json

The core logic operates on an ``ElementTree`` root so it can be unit-tested
in memory without touching the filesystem.
"""

import argparse
import json
import os
import sys

try:
    from defusedxml import ElementTree as ET
except ImportError:
    # ``performance:no_slow_tests`` runs on a bare interpreter with no
    # dependency install (see .gitlab-ci.yml), so fall back to the stdlib
    # parser there. The report is produced by our own ``test:pytest`` job and
    # :func:`_parse_report` refuses DTD/entity declarations outright, so the
    # unhardened parser never sees the entity-expansion payloads defusedxml
    # guards against.
    from xml.etree import ElementTree as ET

from datetime import datetime, timezone
from xml.etree.ElementTree import Element
from typing import Dict, List, Optional, Tuple

DEFAULT_THRESHOLD_SECONDS = 5.0

# Per-test budgets for the two cold, full-tree import sweeps in
# ``test_module_import_safety.py``. Each spawns a throwaway interpreter that
# discovers and imports every module in the production tree, with bytecode
# writing disabled in CI (``PYTHONDONTWRITEBYTECODE: "1"`` in
# ``.gitlab-ci.yml``): 900+ modules compiled and executed on every run. Measured
# on the 4-core build box they take 2.7s and 1.9s with the machine to
# themselves, 7.7s and 3.9s with every core oversubscribed 2x -- the load this
# repository's own ``test:timing_under_load`` job calls deliberately
# oversubscribed -- and 15.7s and 6.9s at 3x. A flat 5s ceiling there gates the
# runner rather than the code, and the box is shared with other tenants, so the
# ceiling is a budget per sweep instead. A genuinely slower regression still
# trips: a sweep that doubles in cost crosses its budget with nothing else
# running (see the reasons below for the measured numbers).
#
# A budget is a partial mitigation, not a cure -- at 3x the heavier sweep is
# past even its ceiling -- but it is the honest one here. The client repository
# scales its ceiling by a calibration test of its own (its suite is in-process,
# so a test in it measures the machine the suite runs on); these sweeps are
# child processes, whose cost the parent process's own timings do not track.
#
# The registry is enforced against the report: an entry whose test id does not
# appear in any report fails the run (see ``main``), so an exemption can never
# silently rot after a rename. This mirrors the enforced-registry convention
# used by ``ci-tooling/license_audit.py`` and
# ``src/tests/unit/test_intentional_sleep_sites.py``.
SLOW_TEST_BUDGETS: Dict[str, Tuple[float, str]] = {
    "src.tests.unit.test_module_import_safety.TestModuleImportsBeforeLoggerSetup::test_every_production_module_imports": (
        10.0,
        "child-interpreter import of the whole tree: 2.7s idle, 7.7s at 2x oversubscription, 15.7s at 3x",
    ),
    "src.tests.unit.test_module_import_safety.TestModuleImportsBeforeConfigSetup::test_missing_config_fails_only_the_documented_way": (
        10.0,
        "child-interpreter import of the whole tree: 1.9s idle, 3.9s at 2x oversubscription, 6.9s at 3x",
    ),
}


def _budget_seconds(
    test_id: str,
    threshold: float,
    budgets: Dict[str, Tuple[float, str]],
) -> float:
    """Return the effective ceiling for *test_id* (its budget, else threshold)."""
    entry = budgets.get(test_id)
    return entry[0] if entry else threshold


def stale_budgets(
    seen_ids: set,
    budgets: Optional[Dict[str, Tuple[float, str]]] = None,
) -> List[str]:
    """Return registered test ids that appear in no report (stale exemptions)."""
    effective = SLOW_TEST_BUDGETS if budgets is None else budgets
    return sorted(tid for tid in effective if tid not in seen_ids)


def _parse_report(xml_path: str) -> Element:
    """Parse a JUnit report, raising ``ValueError`` if it cannot be read."""
    try:
        with open(xml_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError(f"cannot read report {xml_path}: {exc}") from exc
    # Fail closed on DTD/entity declarations: a pytest JUnit report never
    # contains them, and the stdlib parser fallback above is not hardened
    # against entity-expansion attacks.
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError(
            f"cannot read report {xml_path}: DTD/entity declarations are not allowed"
        )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"cannot read report {xml_path}: {exc}") from exc
    if root is None:
        raise ValueError("report has no root element")
    return root


def _scan_tests(root: Element) -> List[Tuple[float, str]]:
    """Return ``(duration_seconds, test_id)`` for every testcase in the report.

    ``test_id`` prefers the file path attribute (pytest's ``junit_family=xunit2``
    sets ``file``); it falls back to ``classname::name`` when absent.
    """
    tests: List[Tuple[float, str]] = []
    for case in root.iter("testcase"):
        try:
            duration = float(case.get("time") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        name = case.get("name") or "<unnamed>"
        file_path = case.get("file") or ""
        classname = case.get("classname") or ""
        test_id = f"{file_path}::{name}" if file_path else f"{classname}::{name}"
        tests.append((duration, test_id))
    return tests


def slow_tests_from_root(
    root: Element,
    threshold: float,
    budgets: Optional[Dict[str, Tuple[float, str]]] = None,
) -> List[Tuple[float, str]]:
    """Return ``(duration_seconds, test_id)`` for every testcase over its budget.

    A testcase is slow when it exceeds its own registered budget, falling back
    to the global *threshold* when it has none.
    """
    effective = SLOW_TEST_BUDGETS if budgets is None else budgets
    return [
        (d, tid)
        for d, tid in _scan_tests(root)
        if d > _budget_seconds(tid, threshold, effective)
    ]


def find_slow_tests(
    xml_path: str,
    threshold: float,
    budgets: Optional[Dict[str, Tuple[float, str]]] = None,
) -> List[Tuple[float, str]]:
    """Parse a JUnit XML report file and return the slow tests in it."""
    return slow_tests_from_root(_parse_report(xml_path), threshold, budgets)


def metrics_from_root(
    root: Element,
    job: str,
    threshold: float,
    budgets: Optional[Dict[str, Tuple[float, str]]] = None,
) -> Dict[str, object]:
    """Aggregate duration metrics from a parsed report root (feeds the CI trend)."""
    effective = SLOW_TEST_BUDGETS if budgets is None else budgets
    tests = _scan_tests(root)
    count = len(tests)
    total = sum(d for d, _ in tests)
    worst_duration, worst_test = max(tests, default=(0.0, ""))
    slow_count = sum(
        1 for d, tid in tests if d > _budget_seconds(tid, threshold, effective)
    )
    return {
        "job": job,
        "pipeline_id": os.environ.get("CI_PIPELINE_ID"),
        "ref": os.environ.get("CI_COMMIT_REF_NAME"),
        # Millisecond precision: locally pipeline_id is absent, so the
        # (job, timestamp) dedupe key in trend_publish.py must be unique per
        # snapshot — a 1-second grain would silently collapse two local runs.
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "test_count": count,
        "total_seconds": round(total, 3),
        "worst_test_seconds": round(worst_duration, 3),
        "worst_test": worst_test[:300],
        "slow_count": slow_count,
    }


def report_metrics(
    xml_path: str,
    job: str,
    threshold: float,
    budgets: Optional[Dict[str, Tuple[float, str]]] = None,
) -> Dict[str, object]:
    """Parse a JUnit report file and aggregate its duration metrics."""
    return metrics_from_root(_parse_report(xml_path), job, threshold, budgets)


def _job_label(idx: int, jobs: List[str], xml_path: str) -> str:
    """Pick the job label for report ``idx``: explicit --job, else CI/env."""
    if idx < len(jobs) and jobs[idx]:
        return jobs[idx]
    env_job = os.environ.get("CI_JOB_NAME")
    if env_job:
        return env_job
    return os.path.splitext(os.path.basename(xml_path))[0]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail when any test in a pytest JUnit XML report is slower than a "
            "threshold, and optionally emit per-job duration metrics."
        )
    )
    parser.add_argument(
        "xml_paths",
        nargs="+",
        metavar="XML",
        help="Path(s) to pytest --junitxml reports",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_SECONDS,
        help=f"Fail when a test exceeds this many seconds (default: {DEFAULT_THRESHOLD_SECONDS:g})",
    )
    parser.add_argument(
        "--job",
        action="append",
        default=[],
        help="Job label for each report, in order (repeatable; defaults to CI_JOB_NAME)",
    )
    parser.add_argument(
        "--metrics-out",
        default=None,
        help="Write a per-job metrics JSON array to this path (for the CI trend)",
    )
    args = parser.parse_args(argv)

    slow: List[Tuple[float, str]] = []
    metrics: List[Dict[str, object]] = []
    seen_ids: set = set()
    for idx, xml_path in enumerate(args.xml_paths):
        # Parse each report exactly once and share the root between the slow
        # scan and the metrics pass (each was previously parsed separately).
        try:
            root = _parse_report(xml_path)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        seen_ids.update(tid for _, tid in _scan_tests(root))
        slow.extend(slow_tests_from_root(root, args.threshold))
        if args.metrics_out:
            metrics.append(
                metrics_from_root(
                    root, _job_label(idx, args.job, xml_path), args.threshold
                )
            )

    # Fail closed on a budget that matches no testcase in any report: the test
    # was renamed or removed, so the exemption no longer applies to anything
    # and must be cleaned up rather than silently carried forever.
    stale = stale_budgets(seen_ids)
    if stale:
        for test_id in stale:
            print(
                f"ERROR: stale slow-test budget (no such testcase in the "
                f"report): {test_id}",
                file=sys.stderr,
            )
        return 2

    for duration, test_id in sorted(slow, reverse=True):
        budget = _budget_seconds(test_id, args.threshold, SLOW_TEST_BUDGETS)
        reason = SLOW_TEST_BUDGETS.get(test_id, (0.0, ""))[1]
        suffix = f"  [{reason}]" if reason else ""
        print(f"SLOW TEST ({duration:.2f}s > {budget:g}s): {test_id}{suffix}")

    # Metrics are written even when the gate trips: a red run's durations are
    # still worth recording (the artifact is uploaded with ``when: always``).
    if args.metrics_out:
        with open(args.metrics_out, "w", encoding="utf-8") as handle:
            json.dump(metrics, handle, indent=2)
        print(f"Wrote metrics for {len(metrics)} job(s) to {args.metrics_out}")

    if slow:
        print(
            f"FAIL: {len(slow)} test(s) slower than their budget "
            f"(default {args.threshold:g}s; worst: {slow[0][0]:.2f}s)",
            file=sys.stderr,
        )
        return 1

    print(f"OK: no test exceeded its budget (default {args.threshold:g}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
