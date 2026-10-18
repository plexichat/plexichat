"""Record the CPU every testcase used, so the slow-test gate can see the runner.

``performance:no_slow_tests`` holds each test to a wall-clock budget, and a wall
clock measures the machine as much as the test. This runner is 4 cores shared
with the client repository's pipeline: when both pushed at once (pipelines #2070
and #2071), the cold import sweeps in ``test_module_import_safety`` -- 2.7s and
1.9s with the box to themselves -- took 22.5s and 15.2s, and three tests that
cost ~2.5s crossed the 5s default. Five red tests, none of them slower.

CPU time does not move that way: the same work is the same CPU seconds whether
the box is idle or shared. So every testcase records the CPU it consumed, its
own plus its children's -- a test that spawns an interpreter is a child process,
and the sweeps above are exactly that -- as the ``cpu_seconds`` property of its
``<testcase>`` entry in the JUnit report. ``judged_seconds`` in
``ci-tooling/check_slow_tests.py`` uses it to judge a test by the work it did
rather than the queue it stood in, and to keep that correction bounded so a test
that is slow by its own work is still caught.

``record_property`` is the channel because a JUnit report carries properties
per testcase and nothing else: pytest writes them as
``<properties><property name="cpu_seconds" .../></properties>`` inside the
``<testcase>``.
"""

import resource
import time

import pytest


def _cpu_seconds() -> float:
    """This process's CPU seconds plus every child process it has reaped."""
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + usage.ru_utime + usage.ru_stime


@pytest.fixture(autouse=True)
def record_cpu_seconds(request, record_property):
    """Attach the CPU this testcase used to its JUnit report entry.

    One pair of readings per test, so the cost is two system calls against a
    suite of thousands -- small enough that it cannot itself move a timing gate
    by more than noise. The reading is taken in teardown, after the fixture's
    cleanup, so a test that spawns a child in teardown is still counted.
    """
    before = _cpu_seconds()
    yield
    used = _cpu_seconds() - before
    # A negative delta means the platform's clock moved backwards (or the
    # reading wrapped); an implausible one is worse than none, so fall back to
    # the wall clock by recording nothing at all.
    if used > 0:
        record_property("cpu_seconds", round(used, 3))
