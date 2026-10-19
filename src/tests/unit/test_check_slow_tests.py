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
