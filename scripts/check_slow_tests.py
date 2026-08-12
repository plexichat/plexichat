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
from defusedxml import ElementTree as ET
from datetime import datetime, timezone
from xml.etree.ElementTree import Element
from typing import Dict, List, Optional, Tuple

DEFAULT_THRESHOLD_SECONDS = 5.0


def _parse_report(xml_path: str) -> Element:
    """Parse a JUnit report, raising ``ValueError`` if it cannot be read."""
    try:
        root = ET.parse(xml_path).getroot()
        if root is None:
            raise ValueError("report has no root element")
        return root
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"cannot read report {xml_path}: {exc}") from exc


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


def slow_tests_from_root(root: Element, threshold: float) -> List[Tuple[float, str]]:
    """Return ``(duration_seconds, test_id)`` for every testcase over threshold."""
    return [(d, tid) for d, tid in _scan_tests(root) if d > threshold]


def find_slow_tests(xml_path: str, threshold: float) -> List[Tuple[float, str]]:
    """Parse a JUnit XML report file and return the slow tests in it."""
    return slow_tests_from_root(_parse_report(xml_path), threshold)


def metrics_from_root(root: Element, job: str, threshold: float) -> Dict[str, object]:
    """Aggregate duration metrics from a parsed report root (feeds the CI trend)."""
    tests = _scan_tests(root)
    count = len(tests)
    total = sum(d for d, _ in tests)
    worst_duration, worst_test = max(tests, default=(0.0, ""))
    slow_count = sum(1 for d, _ in tests if d > threshold)
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


def report_metrics(xml_path: str, job: str, threshold: float) -> Dict[str, object]:
    """Parse a JUnit report file and aggregate its duration metrics."""
    return metrics_from_root(_parse_report(xml_path), job, threshold)


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
    for idx, xml_path in enumerate(args.xml_paths):
        # Parse each report exactly once and share the root between the slow
        # scan and the metrics pass (each was previously parsed separately).
        try:
            root = _parse_report(xml_path)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        slow.extend(slow_tests_from_root(root, args.threshold))
        if args.metrics_out:
            metrics.append(
                metrics_from_root(
                    root, _job_label(idx, args.job, xml_path), args.threshold
                )
            )

    for duration, test_id in sorted(slow, reverse=True):
        print(f"SLOW TEST ({duration:.2f}s > {args.threshold:g}s): {test_id}")

    # Metrics are written even when the gate trips: a red run's durations are
    # still worth recording (the artifact is uploaded with ``when: always``).
    if args.metrics_out:
        with open(args.metrics_out, "w", encoding="utf-8") as handle:
            json.dump(metrics, handle, indent=2)
        print(f"Wrote metrics for {len(metrics)} job(s) to {args.metrics_out}")

    if slow:
        print(
            f"FAIL: {len(slow)} test(s) slower than {args.threshold:g}s "
            f"(worst: {slow[0][0]:.2f}s)",
            file=sys.stderr,
        )
        return 1

    print(f"OK: no test exceeded {args.threshold:g}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
