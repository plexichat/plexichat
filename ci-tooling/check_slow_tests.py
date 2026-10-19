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

A ``--job`` may carry a ceiling of its own, ``--job NAME=SECONDS``, the way the
client repository's gate does. The database-backed suite is why: it drives real
Postgres and Valkey with multi-worker simulations whose cost is wall-clock
(worker restarts, TTL waits) rather than CPU, so its heaviest test measured
4.01s with the box idle against a 5s default a loaded runner would cross. A
per-test budget still takes precedence over the job's ceiling.

A wall clock also measures the *runner*, and this one is 4 cores shared with the
other repository's pipeline. When both pushed at once (pipelines #2070 and
#2071), the import sweeps that take 2.7s and 1.9s on an idle box took 22.5s and
15.2s, and three 2.5s tests crossed 5s -- five false reds from the queue, not
from the code. The verdict stays the wall clock, though: how long a test takes
is what the gate is for, and how much CPU it burned is never a pass/fail
criterion -- a test that uses every core it can get is fast or slow on its
duration, not on its appetite. What the queue cost is measured separately, as
the box's 1-minute load average while the suite ran (``--load-log``), and a
job's ceiling is scaled by it: see :func:`load_room_factor`. An idle box
reports load 1 and every budget is the one registered in
``SLOW_TEST_BUDGETS``; a box carrying another pipeline's work scales the
ceiling by the queue it was under, capped at ``MAX_LOAD_FACTOR`` so a
permanently loaded runner cannot hide a regression.

Usage::

    python scripts/check_slow_tests.py test-results.xml test-results-db.xml \\
        --threshold 5.0 --job test:pytest --job test:database_backends=15 \\
        --metrics-out test-durations.json

The core logic operates on an ``ElementTree`` root so it can be unit-tested
in memory without touching the filesystem.
"""

import argparse
import json
import math
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

# How far a busy box may scale a ceiling (see :func:`load_room_factor`).
# ``MAX_LOAD_FACTOR`` bounds the allowance a run's load samples can buy: at the
# cap a test still has to take four times its registered budget to fail, so a
# permanently loaded runner cannot hide a regression, while a box running two
# pipelines at once -- the load of 2 on 4 cores that redded #2070 -- is covered.
MAX_LOAD_FACTOR = 4.0

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
# The budget is what the sweep costs, not what the worst queue cost: the load
# scaling below raises the ceiling a sweep is held to by the load its job
# measured, so 15.7s on a box at 3x is judged against a 3x budget (30s) rather
# than the idle 10s. That is what keeps the budget meaningful on a runner shared
# with another repository's pipeline, and why these entries can stay at the cost
# of the work instead of climbing to cover the worst queue. The client
# repository's gate scales the same way, from the same ``/proc/loadavg``
# samples.
#
# The registry is enforced against the report: an entry whose test id does not
# appear in any report fails the run (see ``main``), so an exemption can never
# silently rot after a rename. This mirrors the enforced-registry convention
# used by ``ci-tooling/license_audit.py`` and
# ``src/tests/unit/test_intentional_sleep_sites.py``.
# ``test_openapi_snowflake_ids`` used to be the other kind of offender: it
# rebuilt the app and its whole document for each of its eight read-only checks,
# 5.0s-6.8s apiece on pipeline #2046's runner and the reason six tests redded
# ``performance:no_slow_tests`` there. It now builds the document once and the
# checks cost hundredths of a second, but that one build is the whole API's
# OpenAPI document (537 operations over 421 component schemas) and cannot be made
# cheap by ordering the fixtures differently -- so it is a named test with a
# budget, and the module's six assertions run free behind it.
SLOW_TEST_BUDGETS: Dict[str, Tuple[float, str]] = {
    "src.tests.unit.test_module_import_safety.TestModuleImportsBeforeLoggerSetup::test_every_production_module_imports": (
        10.0,
        "child-interpreter import of the whole tree: 2.7s idle, 7.7s at 2x oversubscription, 15.7s at 3x",
    ),
    "src.tests.unit.test_module_import_safety.TestModuleImportsBeforeConfigSetup::test_missing_config_fails_only_the_documented_way": (
        10.0,
        "child-interpreter import of the whole tree: 1.9s idle, 3.9s at 2x oversubscription, 6.9s at 3x",
    ),
    "src.tests.api.test_openapi_snowflake_ids::test_the_document_covers_the_api": (
        20.0,
        "builds the whole API's OpenAPI document for the module: 5.0-6.8s per build on "
        "the runner (#2046, which paid it eight times), 10-13s idle on the build box, "
        "27-31s with two pipelines sharing it",
    ),
    # The three tests that sweep the real repository (1,886 modules read and
    # parsed) are the same class as the import sweeps above: their cost is the
    # size of the tree, not the code under test, so they are priced per sweep
    # rather than by the 5s default. They measured 1.25-1.42s in-suite with the
    # box idle, and 5.06s/5.12s in pipeline #2060 -- the run that redded this
    # gate with all three a hair over the default -- where test:pytest runs
    # four files at a time on four cores and each sweep competes with three
    # other workers. Under the deliberate 3x oversubscription of
    # test:timing_under_load they measured 8.2s/14.4s/15.2s, so like the import
    # sweeps they are past the budget at that load; the ceiling is for the
    # suite's own concurrency, and a sweep that doubles at rest still trips it.
    "src.tests.security.test_audit_failopen::test_real_repository_manager_writes_all_resolve_the_caller": (
        10.0,
        "sweeps every manager in src/core: 1.42s idle, 5.12s at -n 4 on the runner "
        "(#2060), 14.4s at 3x oversubscription",
    ),
    "src.tests.security.test_audit_failopen::test_real_repository_has_no_facade_probe_grants": (
        10.0,
        "sweeps every module under src for facade probes: 1.25s idle, 5.06s at -n 4 "
        "on the runner (#2060), 8.2s at 3x oversubscription",
    ),
    "src.tests.security.test_audit_failopen::test_real_repository_routes_all_authenticate_and_authorize": (
        10.0,
        "inventories every route under src/api/routes: 1.25s idle, 15.2s at 3x "
        "oversubscription",
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


def load_room_factor(
    lines: List[str], max_factor: float = MAX_LOAD_FACTOR
) -> Optional[float]:
    """The factor a busy box justifies, from its 1-minute load samples.

    Each line is ``<load average> <cores>``, written by a test job's sampler
    every couple of seconds while the suite ran (see ``test:pytest`` in
    ``.gitlab-ci.yml``). The busiest sample wins: load 8 on 4 cores is a factor
    of 2, which is what the two pipelines sharing this runner produced when
    #2071 redded tests that cost half their measured time.

    The load average counts every runnable task, not just this job's, which is
    the part of a wall clock nothing inside the job can see. It is clamped to
    ``[1, max_factor]``: a factor never tightens a ceiling (a test is never
    judged by more than its registered budget), and a permanently loaded box
    cannot buy more than the cap.

    :param lines: The sampler's output, one ``LOAD CORES`` sample per line.
    :param max_factor: The largest factor the samples may justify.
    :returns: The factor, or ``None`` when no line parses (no sampler).
    """
    worst: Optional[float] = None
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            load = float(parts[0])
            cores = int(parts[1])
        except ValueError:
            continue
        if not math.isfinite(load) or load < 0 or cores < 1:
            continue
        ratio = load / cores
        if worst is None or ratio > worst:
            worst = ratio
    if worst is None:
        return None
    return round(min(max(worst, 1.0), max_factor), 2)


def load_factor_from_log(
    path: str, max_factor: float = MAX_LOAD_FACTOR
) -> Optional[float]:
    """Read a job's load samples from disk, or ``None`` when there are none.

    A job that predates the sampler (or a locally assembled report set) has no
    log: the job is then held to its registered budget, which is not a reason
    to refuse the run.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return load_room_factor(handle.read().splitlines(), max_factor)
    except OSError as exc:
        print(f"check_slow_tests: no load samples in {path}: {exc}", file=sys.stderr)
        return None


def _scan_tests(root: Element) -> List[Tuple[float, str]]:
    """Return ``(duration_seconds, test_id)`` for every testcase.

    ``test_id`` prefers the file path attribute (pytest's ``junit_family=xunit2``
    sets ``file``); it falls back to ``classname::name`` when absent. The
    duration is the report's own wall clock, which is the only thing the gate
    judges.
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
    factor: float = 1.0,
) -> List[Tuple[float, str]]:
    """Return ``(duration, test_id)`` for every testcase over its budget.

    A testcase is slow when its wall clock exceeds its own registered budget,
    falling back to the global *threshold* when it has none. *factor* is the
    load allowance for the job the report came from (see
    :func:`load_room_factor`): it multiplies the ceiling, so a slice of the
    queue cannot red the job, while the duration reported is still the test's
    own.
    """
    effective = SLOW_TEST_BUDGETS if budgets is None else budgets
    slow: List[Tuple[float, str]] = []
    for duration, test_id in _scan_tests(root):
        if duration > _budget_seconds(test_id, threshold, effective) * factor:
            slow.append((duration, test_id))
    return slow


def find_slow_tests(
    xml_path: str,
    threshold: float,
    budgets: Optional[Dict[str, Tuple[float, str]]] = None,
    factor: float = 1.0,
) -> List[Tuple[float, str]]:
    """Parse a JUnit XML report file and return the slow tests in it."""
    return slow_tests_from_root(_parse_report(xml_path), threshold, budgets, factor)


def metrics_from_root(
    root: Element,
    job: str,
    threshold: float,
    budgets: Optional[Dict[str, Tuple[float, str]]] = None,
    factor: float = 1.0,
) -> Dict[str, object]:
    """Aggregate duration metrics from a parsed report root (feeds the CI trend)."""
    effective = SLOW_TEST_BUDGETS if budgets is None else budgets
    tests = _scan_tests(root)
    count = len(tests)
    total = sum(duration for duration, _ in tests)
    worst_duration, worst_test = max(tests, default=(0.0, ""))
    slow_count = sum(
        1
        for duration, tid in tests
        if duration > _budget_seconds(tid, threshold, effective) * factor
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
        # The ceiling this job was actually judged by, so a run reads back as the
        # rule it was held to: the database suite's 15s is not test:pytest's 5s.
        "ceiling_seconds": round(threshold * factor, 3),
        # The allowance its load samples bought, so a scaled ceiling can be read
        # back later; the trend merges metric objects whole, so an extra key is
        # harmless. 1 means the box was idle and the registered budget applied.
        "load_factor": factor,
    }


def report_metrics(
    xml_path: str,
    job: str,
    threshold: float,
    budgets: Optional[Dict[str, Tuple[float, str]]] = None,
    factor: float = 1.0,
) -> Dict[str, object]:
    """Parse a JUnit report file and aggregate its duration metrics."""
    return metrics_from_root(_parse_report(xml_path), job, threshold, budgets, factor)


def _job_label(idx: int, jobs: List[Tuple[str, Optional[float]]], xml_path: str) -> str:
    """Pick the job label for report ``idx``: explicit --job, else CI/env."""
    if idx < len(jobs) and jobs[idx][0]:
        return jobs[idx][0]
    env_job = os.environ.get("CI_JOB_NAME")
    if env_job:
        return env_job
    return os.path.splitext(os.path.basename(xml_path))[0]


def _job_ceiling(
    idx: int,
    jobs: List[Tuple[str, Optional[float]]],
    threshold: float,
) -> float:
    """The ceiling report ``idx`` is held to: its own, else ``--threshold``."""
    if idx < len(jobs) and jobs[idx][1] is not None:
        return float(jobs[idx][1])
    return threshold


def parse_job_spec(spec: str) -> Tuple[str, Optional[float]]:
    """Split a ``--job`` argument into ``(name, ceiling)``.

    ``NAME`` alone means the job is held to ``--threshold``; ``NAME=SECONDS``
    gives it a ceiling of its own. A malformed argument is a usage error rather
    than a silently ignored flag: a mistyped ceiling would leave the job judged
    by a threshold nobody meant to apply.
    """
    name, separator, seconds = spec.partition("=")
    if not name:
        raise ValueError(f"--job {spec!r}: expected NAME or NAME=SECONDS")
    if not separator:
        return name, None
    try:
        ceiling = float(seconds)
    except ValueError as exc:
        raise ValueError(
            f"--job {spec!r}: {seconds!r} is not a number of seconds"
        ) from exc
    if not math.isfinite(ceiling) or ceiling < 0:
        raise ValueError(
            f"--job {spec!r}: the ceiling must be a finite, non-negative number"
        )
    return name, ceiling


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
        help=(
            "Job label for each report, in order, optionally with a ceiling of its own "
            "(NAME or NAME=SECONDS; repeatable; defaults to CI_JOB_NAME)"
        ),
    )
    parser.add_argument(
        "--load-log",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Load samples for each report, in order, as written by the test job "
            "(repeatable; a report without one is held to its registered budget)"
        ),
    )
    parser.add_argument(
        "--metrics-out",
        default=None,
        help="Write a per-job metrics JSON array to this path (for the CI trend)",
    )
    args = parser.parse_args(argv)
    try:
        job_specs = [parse_job_spec(spec) for spec in args.job]
    except ValueError as exc:
        # Exit 2, the same as an unreadable report: the request is wrong, and
        # judging anything under a ceiling that was not understood is worse.
        parser.error(str(exc))
    if len(args.load_log) > len(args.xml_paths):
        parser.error(
            f"{len(args.load_log)} --load-log path(s) for "
            f"{len(args.xml_paths)} report(s)"
        )

    # Each offender carries the ceiling of the report it came from, scaled by the
    # load that report's job measured, so the printed budget is the one it was
    # actually judged by. The duration is always the test's own wall clock: the
    # load can only widen the ceiling, never red a test on its own.
    slow: List[Tuple[float, str, float, float]] = []
    metrics: List[Dict[str, object]] = []
    seen_ids: set = set()
    ceilings: List[Tuple[str, float, float]] = []
    for idx, xml_path in enumerate(args.xml_paths):
        # Parse each report exactly once and share the root between the slow
        # scan and the metrics pass (each was previously parsed separately).
        try:
            root = _parse_report(xml_path)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        job = _job_label(idx, job_specs, xml_path)
        nominal = _job_ceiling(idx, job_specs, args.threshold)
        factor = (
            load_factor_from_log(args.load_log[idx])
            if idx < len(args.load_log)
            else None
        ) or 1.0
        ceilings.append((job, round(nominal * factor, 3), factor))
        tests = _scan_tests(root)
        seen_ids.update(tid for _, tid in tests)
        for duration, test_id in tests:
            budget = _budget_seconds(test_id, nominal, SLOW_TEST_BUDGETS) * factor
            if duration > budget:
                slow.append((duration, test_id, budget, factor))
        if args.metrics_out:
            metrics.append(metrics_from_root(root, job, nominal, factor=factor))

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

    def _scaled(ceiling: float, factor: float) -> str:
        return f"{ceiling:g}s" + (f" x{factor:g} load factor" if factor != 1 else "")

    # How the ceiling is named in the verdict: one description when every report
    # is held to the same one (the wording this gate has always used), and per
    # job when they are not, so a reader can tell which ceiling binds which
    # report -- and which of them was scaled by a busy box.
    if len({(ceiling, factor) for _, ceiling, factor in ceilings}) <= 1:
        if ceilings:
            only, only_factor = ceilings[0][1], ceilings[0][2]
        else:
            only, only_factor = args.threshold, 1.0
        if only_factor == 1 and only == args.threshold:
            where = f"default {_scaled(only, only_factor)}"
        else:
            where = f"{_scaled(only, only_factor)} for every job"
    else:
        where = ", ".join(
            f"{job} {_scaled(ceiling, factor)}" for job, ceiling, factor in ceilings
        )

    for duration, test_id, budget, factor in sorted(slow, reverse=True):
        reason = SLOW_TEST_BUDGETS.get(test_id, (0.0, ""))[1]
        suffix = f"  [{reason}]" if reason else ""
        scaled = f" x{factor:g} load factor" if factor != 1 else ""
        print(f"SLOW TEST ({duration:.2f}s > {budget:g}s{scaled}): {test_id}{suffix}")

    # Metrics are written even when the gate trips: a red run's durations are
    # still worth recording (the artifact is uploaded with ``when: always``).
    if args.metrics_out:
        with open(args.metrics_out, "w", encoding="utf-8") as handle:
            json.dump(metrics, handle, indent=2)
        print(f"Wrote metrics for {len(metrics)} job(s) to {args.metrics_out}")

    if slow:
        print(
            f"FAIL: {len(slow)} test(s) slower than their budget "
            f"({where}; worst: {slow[0][0]:.2f}s)",
            file=sys.stderr,
        )
        return 1

    print(f"OK: no test exceeded its budget ({where})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
