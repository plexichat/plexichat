"""Unit tests for the CI slow-test gate and its per-test budget registry.

``ci-tooling/check_slow_tests.py`` gates ``test:pytest`` and
``test:database_backends`` on per-test duration. Each report is held to the
ceiling its ``--job`` names (or the global threshold), and it carries a small
registry of tests whose runtime is dominated by a cold, full-tree import and
therefore gets an explicit ceiling instead of the global threshold. These tests
pin the budget logic, the per-job ceilings, and, crucially, the stale-exemption
guard: a registered test id that no longer matches a real testcase must be
reported, so the exemption cannot quietly stop applying.

The gate's core functions operate on a parsed ``ElementTree`` root (see the
script docstring), so the tests build real XML and never mock the parsing.
"""

import importlib.util
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_GATE_PATH = _PROJECT_ROOT / "ci-tooling" / "check_slow_tests.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_slow_tests", _GATE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {_GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_gate()

# The registry is keyed by the exact id ``_scan_tests`` derives from JUnit. The
# report has no ``file`` attribute here, so the id is ``classname::name``.
REGISTERED = (
    "src.tests.unit.test_module_import_safety."
    "TestModuleImportsBeforeConfigSetup::test_missing_config_fails_only_the_documented_way"
)
REGISTERED_CLASS, _, REGISTERED_NAME = REGISTERED.partition("::")


def _report(cases):
    """Build a JUnit ``testsuite`` root from ``(classname, name, time)`` tuples."""
    cases_xml = "".join(
        f'<testcase classname="{classname}" name="{name}" time="{time}" />'
        for classname, name, time in cases
    )
    return check.ET.fromstring(f"<testsuite>{cases_xml}</testsuite>")


def _write_report(path, cases):
    """Write a JUnit report to disk, the way pytest leaves one behind."""
    cases_xml = "".join(
        f'<testcase classname="{classname}" name="{name}" time="{time}" />'
        for classname, name, time in cases
    )
    path.write_text(
        f'<?xml version="1.0"?><testsuite>{cases_xml}</testsuite>', encoding="utf-8"
    )
    return path


def _registered_cases():
    """The registry's ids as report cases, so the stale-budget guard passes.

    The gate refuses a run whose registry names no testcase at all (that is the
    guard that stops an exemption rotting after a rename), so every synthetic
    report here has to carry them.
    """
    cases = []
    for test_id in check.SLOW_TEST_BUDGETS:
        classname, _, name = test_id.partition("::")
        cases.append((classname, name, 0.1))
    return cases


def test_unregistered_over_threshold_is_flagged():
    """A test with no budget is judged against the global threshold."""
    root = _report([("x.Test", "test_slow", 6.0)])
    assert check.slow_tests_from_root(root, 5.0) == [(6.0, "x.Test::test_slow")]


def test_registered_under_its_budget_is_not_flagged():
    """A registered sweep under its own budget passes despite exceeding 5s."""
    root = _report([(REGISTERED_CLASS, REGISTERED_NAME, 6.0)])
    assert check.slow_tests_from_root(root, 5.0) == []


def test_registered_over_its_budget_is_flagged():
    """A registered sweep that regresses past its ceiling still fails."""
    # Read the registered budget rather than repeating it, so raising a sweep's
    # ceiling (as #2082's shared-box measurement did) cannot quietly turn this
    # assertion into a no-op.
    budget = check.SLOW_TEST_BUDGETS[REGISTERED][0]
    root = _report([(REGISTERED_CLASS, REGISTERED_NAME, budget + 1.0)])
    assert check.slow_tests_from_root(root, 5.0) == [(budget + 1.0, REGISTERED)]


def test_stale_budget_is_reported():
    """A registry entry matching no testcase is surfaced, not silently kept."""
    assert set(check.stale_budgets({"other.Test::test_x"})) == set(
        check.SLOW_TEST_BUDGETS
    )


def test_matching_budgets_are_not_stale():
    """Entries that do match a testcase are not reported as stale."""
    assert check.stale_budgets(set(check.SLOW_TEST_BUDGETS)) == []


def test_registry_covers_the_real_sweeps():
    """Every registered id still names a real class and test in the suite.

    The stale-budget guard relies on the registered id matching the report;
    this proves the same id matches the source, so a rename is caught here. An
    id is ``module[.Class]::test`` and now covers a module-level test as well
    as the two sweeps inside a class, so the module is resolved out of the
    id's own path rather than assumed.
    """
    for test_id in check.SLOW_TEST_BUDGETS:
        classname, _, name = test_id.partition("::")
        parts = classname.split(".")
        module = _PROJECT_ROOT.joinpath(*parts).with_suffix(".py")
        if not module.is_file():
            module = _PROJECT_ROOT.joinpath(*parts[:-1]).with_suffix(".py")
            assert module.is_file(), test_id
            assert f"class {parts[-1]}" in module.read_text(encoding="utf-8"), test_id
        assert f"def {name}" in module.read_text(encoding="utf-8"), test_id


@pytest.mark.unit
def test_gate_still_flags_an_unbudgeted_regression():
    """End-to-end: an ordinary 6s test is still a gate failure."""
    root = _report(
        [
            ("x.Test", "test_fast", 0.1),
            ("x.Test", "test_regressed", 6.0),
        ]
    )
    slow = check.slow_tests_from_root(root, 5.0)
    assert slow == [(6.0, "x.Test::test_regressed")]


def test_a_job_may_carry_its_own_ceiling():
    """``NAME`` is held to the threshold; ``NAME=SECONDS`` to its own number."""
    assert check.parse_job_spec("test:pytest") == ("test:pytest", None)
    assert check.parse_job_spec("test:database_backends=15") == (
        "test:database_backends",
        15.0,
    )


def test_a_malformed_job_argument_is_a_usage_error():
    """A mistyped ceiling must not leave the job judged by some other number."""
    for spec in ("test:db=abc", "test:db=-1", "test:db=inf", "=15", ""):
        with pytest.raises(ValueError):
            check.parse_job_spec(spec)


def test_a_ceiling_binds_only_the_report_it_names():
    """The database suite's ceiling does not loosen test:pytest."""
    jobs = [("test:pytest", None), ("test:database_backends", 15.0)]
    assert check._job_ceiling(0, jobs, 5.0) == 5.0
    assert check._job_ceiling(1, jobs, 5.0) == 15.0
    assert check._job_ceiling(2, jobs, 5.0) == 5.0, (
        "a report with no --job keeps the threshold"
    )


def test_each_report_is_judged_by_its_own_ceiling(tmp_path, capsys):
    """A 6s test fails under 5s and passes under the 15s of its own job."""
    pytest_report = _write_report(
        tmp_path / "test-results.xml",
        _registered_cases() + [("x.Test", "test_six_seconds", 6.0)],
    )
    db_report = _write_report(
        tmp_path / "test-results-db.xml",
        _registered_cases() + [("y.Test", "test_six_seconds", 6.0)],
    )

    code = check.main(
        [
            str(pytest_report),
            str(db_report),
            "--threshold",
            "5",
            "--job",
            "test:pytest",
            "--job",
            "test:database_backends=15",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert "x.Test::test_six_seconds" in captured.out
    assert "y.Test::test_six_seconds" not in captured.out, (
        "the database report's 6s test is inside its own 15s ceiling"
    )
    assert "test:pytest 5s, test:database_backends 15s" in captured.err

    # The same 6s test, judged alone by the job that owns it, is not an offender.
    code = check.main(
        [
            str(db_report),
            "--threshold",
            "5",
            "--job",
            "test:database_backends=15",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert "15s for every job" in captured.out


def test_load_samples_use_the_busiest_moment():
    """The worst sample decides: a quiet start does not excuse a busy run."""
    assert check.load_room_factor(["0.4 4", "8.0 4", "1.2 4"]) == 2.0
    # 2 of 4 cores busy is half a job: the factor floors at 1, not 0.5.
    assert check.load_room_factor(["2.0 4"]) == 1.0


def test_load_samples_never_tighten_a_ceiling():
    """An idle box reports load 1, and a negative ratio cannot shrink a budget."""
    assert check.load_room_factor(["0.0 4", "1.0 4"]) == 1.0
    assert check.load_room_factor(["0.0 4"]) == 1.0


def test_the_load_allowance_is_capped():
    """A permanently loaded box buys at most the cap, not an exemption."""
    assert check.load_room_factor(["64.0 4"]) == check.MAX_LOAD_FACTOR
    # 8 runnable tasks on 2 cores is a 4x ratio, still held to the cap given.
    assert check.load_room_factor(["8.0 2"], max_factor=1.5) == 1.5


def test_malformed_load_samples_are_ignored():
    """A missing or unreadable sampler degrades to the registered budget."""
    assert check.load_room_factor([]) is None
    assert check.load_room_factor(["", "garbage", "1 2 3", "nan 4", "4 0"]) is None
    assert check.load_room_factor(["garbage", "3.0 3"]) == 1.0


def test_a_missing_load_log_is_not_a_reason_to_refuse(tmp_path, capsys):
    """A job (or a local report set) without samples is still judged."""
    assert check.load_factor_from_log(str(tmp_path / "absent.log")) is None
    assert "no load samples" in capsys.readouterr().err


def test_a_contended_test_is_held_to_the_scaled_ceiling():
    """9s on a box at 2x load is inside a 5s ceiling scaled to 10s."""
    root = _report([("x.Test", "test_queued", 9.0)])
    assert check.slow_tests_from_root(root, 5.0) == [(9.0, "x.Test::test_queued")]
    assert check.slow_tests_from_root(root, 5.0, factor=2.0) == []


def test_cpu_usage_is_not_a_failure_reason():
    """A test is judged by its duration alone: a fast test never fails on CPU.

    The gate is handed a report of a test that took 2s while burning the whole
    box (parallel children, a spinning helper), and no load samples. Nothing
    about how much CPU it used can red it, because the verdict is the wall
    clock.
    """
    root = check.ET.fromstring(
        '<testsuite><testcase classname="x.Test" name="test_spin" time="2.0">'
        '<properties><property name="cpu_seconds" value="8.0" /></properties>'
        "</testcase></testsuite>"
    )
    assert check.slow_tests_from_root(root, 5.0) == []


def test_a_verdict_shows_the_scaled_ceiling(tmp_path, capsys):
    """The log has to say which ceiling (and load) a test was judged by."""
    report = _write_report(
        tmp_path / "r.xml", _registered_cases() + [("x.Test", "test_slow", 9.0)]
    )
    load_log = tmp_path / "load.log"
    load_log.write_text("0.5 4\n6.0 4\n", encoding="utf-8")
    code = check.main([str(report), "--threshold", "5", "--load-log", str(load_log)])
    captured = capsys.readouterr()
    assert code == 1, captured.out + captured.err
    assert (
        "SLOW TEST (9.00s > 7.5s x1.5 load factor): x.Test::test_slow" in captured.out
    )


def test_metrics_record_the_load_factor(tmp_path):
    """A scaled run reads back as the ceiling it was actually held to."""
    report = _write_report(tmp_path / "r.xml", _registered_cases())
    metrics = check.metrics_from_root(
        check._parse_report(str(report)), "test:pytest", 5.0, factor=2.0
    )
    assert metrics["load_factor"] == 2.0
    assert metrics["ceiling_seconds"] == 10.0


def test_more_load_logs_than_reports_exits_two(tmp_path):
    """The pairing is positional, so an unpaired log is a usage error."""
    report = _write_report(tmp_path / "r.xml", _registered_cases())
    log = tmp_path / "load.log"
    log.write_text("1.0 4\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        check.main([str(report), "--load-log", str(log), "--load-log", str(log)])
    assert exit_info.value.code == 2


def test_an_unreadable_job_argument_exits_two(tmp_path):
    """A bad --job is refused (exit 2) instead of judged under a guess."""
    report = _write_report(
        tmp_path / "r.xml", _registered_cases() + [("x.Test", "test_fast", 0.1)]
    )
    with pytest.raises(SystemExit) as exit_info:
        check.main([str(report), "--job", "test:db=soon"])
    assert exit_info.value.code == 2


def test_a_report_with_no_testcases_is_refused(tmp_path, capsys):
    """A gate over an empty report must not report the suite as green.

    ``OK: no test exceeded its budget`` is vacuously true when the report holds
    no testcase: a job that produced an empty artifact, or a reporter that
    changed its output shape, would otherwise pass while measuring nothing.
    """
    report = tmp_path / "empty.xml"
    report.write_text('<?xml version="1.0"?><testsuite tests="0" />', encoding="utf-8")

    code = check.main([str(report), "--threshold", "5"])
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    assert "contains no testcases" in captured.err
    assert "OK:" not in captured.out


def test_an_unreadable_duration_is_refused_not_read_as_zero(tmp_path, capsys):
    """A testcase with no measurable time is unknown, not instantaneous.

    Coercing the missing attribute to 0.0 made every testcase in a truncated
    (or reshaped) report look fast, which is the one failure mode a duration
    gate cannot afford: it turns a broken report into a green pipeline.
    """
    report = tmp_path / "no-time.xml"
    report.write_text(
        '<?xml version="1.0"?><testsuite>'
        '<testcase classname="x.Test" name="test_unmeasured" />'
        "</testsuite>",
        encoding="utf-8",
    )

    code = check.main([str(report), "--threshold", "5"])
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    assert "no usable time attribute" in captured.err
    assert "x.Test::test_unmeasured" in captured.err

    # The core scan refuses on its own too, so no caller can silently read the
    # case as 0.0 by going around ``main``.
    with pytest.raises(ValueError, match="no usable time attribute"):
        check.slow_tests_from_root(check._parse_report(str(report)), 5.0)


@pytest.mark.parametrize("time", ["nan", "inf", "-2.5", "soon"])
def test_a_non_finite_or_negative_duration_is_refused(time):
    """A bogus time attribute is not a fast test either."""
    root = check.ET.fromstring(
        f'<testsuite><testcase classname="x.Test" name="t" time="{time}" /></testsuite>'
    )
    with pytest.raises(ValueError, match="no usable time attribute"):
        check.slow_tests_from_root(root, 5.0)


def test_a_report_of_only_skipped_testcases_is_refused(tmp_path, capsys):
    """A report that is not empty can still have measured nothing.

    Every test in the selection skipping -- a missing backend, a platform
    guard, a worker that never started -- leaves a well-formed report of
    0-second testcases. ``OK: no test exceeded its budget`` over that is the
    same green light an empty report used to earn.
    """
    report = tmp_path / "all-skipped.xml"
    report.write_text(
        '<?xml version="1.0"?><testsuite tests="2">'
        '<testcase classname="x.Test" name="test_pg" time="0.0">'
        '<skipped message="postgres not reachable" /></testcase>'
        '<testcase classname="x.Test" name="test_valkey" time="0.0">'
        '<skipped message="valkey not reachable" /></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )

    code = check.main([str(report), "--threshold", "5"])
    captured = capsys.readouterr()
    assert code == 2, captured.out + captured.err
    assert "contains only skipped testcases" in captured.err
    assert "OK:" not in captured.out


def test_an_xfail_counts_as_having_run(tmp_path, capsys):
    """pytest writes an xfail as ``<skipped type=\"pytest.xfail\">`` after running it.

    Refusing such a report would red a suite that did its work, so only a
    skipped testcase without that type is treated as never having run.
    """
    case = check.ET.fromstring(
        '<testcase classname="x.Test" name="test_known_bug" time="0.2">'
        '<skipped type="pytest.xfail" message="expected failure" /></testcase>'
    )
    assert check._did_not_run(case) is False

    registered = "".join(
        f'<testcase classname="{classname}" name="{name}" time="{time}" />'
        for classname, name, time in _registered_cases()
    )
    report = tmp_path / "xfail.xml"
    report.write_text(
        f'<?xml version="1.0"?><testsuite>{registered}'
        '<testcase classname="x.Test" name="test_known_bug" time="0.2">'
        '<skipped type="pytest.xfail" message="expected failure" /></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )

    assert check.main([str(report), "--threshold", "5"]) == 0, capsys.readouterr().err


def test_a_skip_alongside_a_test_that_ran_is_allowed(tmp_path):
    """One executed test is enough: a normal report mixes pass and skip."""
    registered = "".join(
        f'<testcase classname="{classname}" name="{name}" time="{time}" />'
        for classname, name, time in _registered_cases()
    )
    report = tmp_path / "r.xml"
    report.write_text(
        f'<?xml version="1.0"?><testsuite>{registered}'
        '<testcase classname="x.Test" name="test_pg" time="0.0">'
        '<skipped message="postgres not reachable" /></testcase></testsuite>',
        encoding="utf-8",
    )

    assert check.main([str(report), "--threshold", "5"]) == 0


@pytest.mark.parametrize("threshold", ["nan", "inf", "-inf", "-5"])
def test_a_ceiling_that_cannot_be_applied_is_refused(tmp_path, capsys, threshold):
    """A threshold no duration can exceed must not pass the gate.

    ``nan`` compares false against every duration, so ``--threshold nan``
    over a report holding a 15-minute test printed ``OK: no test exceeded its
    budget`` and exited 0 -- a mistyped ceiling switched the gate off.
    ``inf`` is the same hole, and a negative one is a ceiling nobody meant.
    The gate refuses the invocation instead of judging under it.
    """
    report = _write_report(
        tmp_path / "r.xml",
        _registered_cases() + [("x.Test", "test_very_slow", 900.0)],
    )

    with pytest.raises(SystemExit) as exit_info:
        check.main([str(report), "--threshold", threshold])
    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert "--threshold" in captured.err
    assert "OK:" not in captured.out


def test_a_finite_threshold_still_fails_the_same_report(tmp_path, capsys):
    """The control for the refusal above: 900s is slow against a real ceiling."""
    report = _write_report(
        tmp_path / "r.xml",
        _registered_cases() + [("x.Test", "test_very_slow", 900.0)],
    )
    assert check.main([str(report), "--threshold", "5"]) == 1
    assert "test_very_slow" in capsys.readouterr().out


def test_an_empty_metrics_out_path_is_refused(tmp_path, capsys):
    """``--metrics-out ""`` looked like a request and wrote nothing.

    The flag parses to the empty string, which is falsy, so the metrics block
    was skipped while the job's artifact upload went looking for a file the
    run never asked to produce. A path is required or the flag is omitted.
    """
    report = _write_report(tmp_path / "r.xml", _registered_cases())

    with pytest.raises(SystemExit) as exit_info:
        check.main([str(report), "--threshold", "5", "--metrics-out", ""])
    assert exit_info.value.code == 2
    assert "--metrics-out" in capsys.readouterr().err
    assert not (tmp_path / "test-durations.json").exists()


def test_more_jobs_than_reports_exits_two(tmp_path):
    """``--job`` is positional: a ceiling for no report is a usage error."""
    report = _write_report(tmp_path / "r.xml", _registered_cases())
    with pytest.raises(SystemExit) as exit_info:
        check.main(
            [str(report), "--job", "test:pytest", "--job", "test:database_backends=15"]
        )
    assert exit_info.value.code == 2


def test_a_skipped_testcase_without_a_duration_is_not_a_failure():
    """A test that did not run has no duration to be slow with.

    This is the one case the old read-as-zero behaviour got right: pytest
    writes ``time="0.0"`` for a skip, but a reporter that omits it is still
    describing a test that never ran, so it stands at zero rather than
    reddening the gate on a missing measurement.
    """
    root = check.ET.fromstring(
        '<testsuite><testcase classname="x.Test" name="test_skipped">'
        '<skipped message="not on this platform" />'
        "</testcase></testsuite>"
    )
    assert check.slow_tests_from_root(root, 5.0) == []
