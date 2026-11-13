"""Unit tests for the pytest shard map and the gitignore package guard.

Two CI checks earn their existence here.

``ci-tooling/balance_pytest_shards.py`` packs the suite's test files into the
parallel shards by measured duration. Pipeline #2518 showed why file count is
the wrong currency: the hashed split matched counts (382 against 390) and still
ran 341.7s against 286.6s. The map it writes is committed, so the invariant
worth pinning is that it still covers the tree exactly once and that its shard
numbers are real -- a stale map silently deals files by the fallback hash.

``ci-tooling/check_gitignore_packages.py`` exists because
``src/core/selftest/services/data/`` was hidden by the root ``data/`` ignore
rule and never landed: `git add` skipped it silently, the import-only tests
passed locally, and CI never noticed because it never received the package.
These tests pin that the guard sees such packages at all, and that it flags a
path the ignore rules do match.
"""

import importlib.util
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load(name, filename):
    path = _PROJECT_ROOT / "ci-tooling" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shards = _load("balance_pytest_shards", "balance_pytest_shards.py")
guard = _load("check_gitignore_packages", "check_gitignore_packages.py")


@pytest.fixture(autouse=True)
def _in_project_root(monkeypatch):
    """Both tools resolve paths and git rules relative to the repository root."""
    monkeypatch.chdir(_PROJECT_ROOT)


def _map():
    return shards.load_map(str(_PROJECT_ROOT / shards.DEFAULT_MAP))


def test_map_covers_every_shardable_test_file_exactly_once():
    assignment = _map()["files"]
    expected = shards.collect_files()
    assert sorted(expected) == sorted(assignment)
    assert len(set(expected)) == len(expected)


def test_map_never_contains_the_benchmark_module():
    assignment = _map()["files"]
    for excluded in shards.EXCLUDED:
        assert excluded not in assignment


def test_shard_numbers_are_within_range():
    data = _map()
    shards_count = int(data["shards"])
    assert shards_count >= 2
    bad = {
        path: shard
        for path, shard in data["files"].items()
        if not 0 <= int(shard) < shards_count
    }
    assert bad == {}


def test_committed_map_passes_its_own_check(capsys):
    assert shards.check(str(_PROJECT_ROOT / shards.DEFAULT_MAP)) == 0
    assert "OK:" in capsys.readouterr().out


def test_check_reports_a_file_missing_from_the_map(tmp_path, monkeypatch):
    data = _map()
    dropped = sorted(data["files"])[0]
    data["files"].pop(dropped)
    stale = tmp_path / "map.json"
    stale.write_text(__import__("json").dumps(data), encoding="utf-8")
    assert shards.check(str(stale)) == 1


def test_packing_puts_the_heavy_files_on_opposite_sides():
    # Longest first: ``a`` (10s) opens shard 0, the three 1s files then fill
    # shard 1 together. The point is that a single expensive file is not left
    # beside another expensive one just because their names hash the same way.
    weights = {"a": 10.0, "b": 1.0, "c": 1.0, "d": 1.0}
    assignment = shards.pack(sorted(weights), weights, 2)
    assert assignment["a"] != assignment["b"]
    assert assignment["b"] == assignment["c"] == assignment["d"]


def test_unmeasured_files_are_dealt_by_hash():
    assignment = shards.pack(sorted({"x", "y", "z", "w"}), {}, 2)
    assert set(assignment.values()) == {0, 1}


def test_weights_are_summed_per_module_from_classnames(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        "<testsuite>"
        '<testcase classname="src.tests.unit.test_a" name="one" time="1.5" />'
        '<testcase classname="src.tests.unit.test_a.TestThing" name="two" time="2.5" />'
        '<testcase classname="src.tests.unit.test_b" name="three" time="0.5" />'
        "</testsuite>",
        encoding="utf-8",
    )
    weights = shards.weights_from_reports([str(report)])
    assert weights["src/tests/unit/test_a.py"] == pytest.approx(4.0)
    assert weights["src/tests/unit/test_b.py"] == pytest.approx(0.5)


def test_guard_finds_packages_in_the_source_tree():
    packages = guard.find_packages(("src",))
    assert "src/core/selftest/services/data" in packages
    assert "src/core/selftest/services/endpoints/admin" in packages


def test_guard_flags_a_path_the_ignore_rules_match():
    # ``/config/`` is ignored on purpose; the guard must be able to say so,
    # otherwise "no findings" would just mean "never looked".
    assert guard.ignore_rule("config/config.yaml").endswith("config/config.yaml")


def test_guard_reports_no_hidden_packages_in_the_tree():
    assert guard.audit(("src", "ci-tooling", "scripts", "docker")) == []
