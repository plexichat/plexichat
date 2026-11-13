#!/usr/bin/env python3
"""Balance the pytest shards by measured duration rather than by file name.

Pipeline #2518 split the suite with ``zlib.crc32(path) % shards``. That deals
files out evenly by *count* -- 382 against 390 -- and still ran 341.7s against
286.6s, because a file's cost ranges from a hundredth of a second to tens of
seconds and the hash put several of the heavy modules on the same side.

This reads the JUnit reports the shard jobs already upload, sums each test
file's duration, and packs whole files into ``--shards`` buckets
longest-processing-time-first. Files that produced no test cases (fixture
modules, modules pytest.ini deselects) carry no weight and are placed by crc32,
so the assignment stays deterministic and reproducible.

The result is ``ci-tooling/pytest-shard-map.json``, committed and read by the
``test:pytest`` job. New test files are not in it; the job falls back to crc32
for them and ``--check`` reports the drift, so regenerating after a rebalance
is a visible, reviewable change rather than a silent re-deal.

Usage::

    python ci-tooling/balance_pytest_shards.py test-results-0.xml test-results-1.xml
    python ci-tooling/balance_pytest_shards.py --check

Exit codes: 0 = clean/written, 1 = drift (-check only), 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zlib

# The reports are produced by our own ``test:pytest`` job, and defusedxml is
# pinned in requirements.txt, so every job that runs this can parse them: the
# stdlib parser is never needed and never used.
from defusedxml import ElementTree as ET
from typing import Dict, Iterable, List, Optional, Sequence

DEFAULT_MAP = os.path.join("ci-tooling", "pytest-shard-map.json")
TEST_ROOTS = ("src/tests", "src/utils/common_utils/tests")
# The benchmark module is timing-sensitive and runs on its own, in one process,
# inside a single shard; it must never be dealt into the file list.
EXCLUDED = (
    os.path.normpath(os.path.join("src", "tests", "test_database_benchmarks.py")),
)
# How far the two halves may drift before --check complains. The split exists to
# stop one half doing most of the work, not to hit a millisecond.
MAX_IMBALANCE_PERCENT = 5.0


def collect_files(roots: Sequence[str] = TEST_ROOTS) -> List[str]:
    """Every test file the shard job is expected to deal out."""
    files: List[str] = []
    for root in roots:
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in names:
                if name.startswith("test_") and name.endswith(".py"):
                    path = os.path.normpath(os.path.join(dirpath, name))
                    if path not in EXCLUDED:
                        files.append(path)
    return sorted(files)


def module_path(classname: str) -> Optional[str]:
    """The test module a JUnit ``classname`` belongs to, or None.

    pytest writes the module's dotted path in ``classname`` and the test's own
    name in ``name``. For a method it appends the class, so
    ``src.tests.unit.test_a.TestThing`` -- and a plain ``rsplit('.', 1)`` would
    turn that into the *package* for function-level cases, which is how the
    first version of this script measured 523 files and silently left the rest
    to the hash. Test modules here all start with ``test_``, which tells the
    two shapes apart without touching the filesystem.
    """
    parts = classname.split(".")
    while parts and not parts[-1].startswith("test_"):
        parts.pop()
    if not parts or parts[0] != "src":
        return None
    return os.path.normpath("/".join(parts) + ".py")


def weights_from_reports(reports: Iterable[str]) -> Dict[str, float]:
    """Per-file test seconds, summed over every test case in the reports."""
    weights: Dict[str, float] = {}
    for report in reports:
        root = ET.parse(report).getroot()
        for case in root.iter("testcase"):
            path = module_path(case.get("classname") or "")
            if path is None:
                continue
            weights[path] = weights.get(path, 0.0) + float(case.get("time") or 0.0)
    return weights


def pack(
    files: Sequence[str], weights: Dict[str, float], shards: int
) -> Dict[str, int]:
    """Assign whole files to shards, longest first, always to the lightest."""
    load = {shard: 0.0 for shard in range(shards)}
    assignment: Dict[str, int] = {}
    ordered = sorted(files, key=lambda path: (-weights.get(path, 0.0), path))
    for path in ordered:
        cost = weights.get(path, 0.0)
        if cost <= 0.0:
            # No measurement: spread by hash so repeated runs agree, and so the
            # unmeasured files land on both halves rather than all on one.
            shard = zlib.crc32(path.encode()) % shards
        else:
            shard = min(load, key=lambda s: (load[s], s))
        assignment[path] = shard
        load[shard] += cost
    return assignment


def build(reports: Sequence[str], shards: int) -> Dict[str, object]:
    files = collect_files()
    weights = weights_from_reports(reports)
    assignment = pack(files, weights, shards)
    return {
        "shards": shards,
        "source": [os.path.basename(report) for report in reports],
        "files": dict(sorted(assignment.items())),
    }


def write_map(path: str, data: Dict[str, object]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_map(path: str) -> Dict[str, object]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def report(data: Dict[str, object]) -> List[str]:
    """One line per shard: file count and the measured load if we have it."""
    assignment = data.get("files") or {}
    shards = int(data.get("shards") or 0)
    counts = {shard: 0 for shard in range(shards)}
    for shard in assignment.values():
        counts[int(shard)] = counts.get(int(shard), 0) + 1
    return [
        f"shard {shard}: {count} file(s)" for shard, count in sorted(counts.items())
    ]


def check(path: str) -> int:
    """Fail when the committed map no longer covers the tree or is lopsided."""
    if not os.path.exists(path):
        print(f"FAIL: {path} is missing", file=sys.stderr)
        return 1
    data = load_map(path)
    assignment = {str(k): int(v) for k, v in (data.get("files") or {}).items()}
    shards = int(data.get("shards") or 0)
    if shards < 2:
        print(f"FAIL: {path} declares {shards} shard(s)", file=sys.stderr)
        return 1

    expected = set(collect_files())
    missing = sorted(expected - set(assignment))
    extra = sorted(set(assignment) - expected)
    for path_name in missing:
        print(
            f"FAIL: {path_name} is not in the shard map; regenerate it", file=sys.stderr
        )
    for path_name in extra:
        print(
            f"FAIL: {path_name} is in the shard map but is not a shardable test file",
            file=sys.stderr,
        )
    bad = sorted(name for name, shard in assignment.items() if not 0 <= shard < shards)
    for name in bad:
        print(
            f"FAIL: {name} is assigned to a shard that does not exist", file=sys.stderr
        )
    if missing or extra or bad:
        return 1

    counts = {shard: 0 for shard in range(shards)}
    for shard in assignment.values():
        counts[shard] += 1
    sizes = list(counts.values())
    drift = (max(sizes) - min(sizes)) / max(max(sizes), 1) * 100
    print(
        f"OK: {len(assignment)} test file(s) across {shards} shard(s) "
        f"(file-count drift {drift:.1f}%)"
    )
    if drift > MAX_IMBALANCE_PERCENT:
        print(
            f"FAIL: shard file counts differ by {drift:.1f}%, more than "
            f"{MAX_IMBALANCE_PERCENT}%: regenerate the map from fresh reports",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "reports", nargs="*", help="JUnit XML reports from the shard jobs"
    )
    parser.add_argument(
        "-o", "--output", default=DEFAULT_MAP, help=f"map to write ({DEFAULT_MAP})"
    )
    parser.add_argument(
        "-s", "--shards", type=int, default=2, help="number of shards (default: 2)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed map against the tree instead of writing it",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check(args.output)
    if not args.reports:
        parser.error("give at least one JUnit report to balance from")
    if args.shards < 2:
        parser.error("--shards must be at least 2")

    data = build(args.reports, args.shards)
    write_map(args.output, data)
    print(f"wrote {args.output} from {', '.join(data['source'])}")
    for line in report(data):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
