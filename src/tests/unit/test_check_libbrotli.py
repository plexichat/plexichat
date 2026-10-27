"""The Debian watcher for a fixed libbrotli1.

The backend image ships ``libbrotli 1.1.0`` -- inside CVE-2026-111497's
vulnerable range -- only because Debian trixie has published nothing newer.
That is an accepted risk with an expiry date, and this gate is how the expiry
gets noticed. The failure mode worth guarding is the quiet one: a version
comparison that ranks 1.2.0 above 1.10.0 (or an epoch/revision wrong) would
keep reporting "still vulnerable, nothing to do" indefinitely, so the ordering
cases are pinned here rather than assumed.

The script reads a sources.debian.org response; the tests feed it real payload
shapes instead of hitting the network.
"""

import importlib.util
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_WATCH_PATH = _PROJECT_ROOT / "ci-tooling" / "check_libbrotli.py"


def _load_watch():
    spec = importlib.util.spec_from_file_location("check_libbrotli", _WATCH_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {_WATCH_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = _load_watch()


def _payload(*versions_by_suite: tuple[str, list[str]]) -> dict:
    return {
        "package": "brotli",
        "versions": [
            {"area": "main", "suites": suites, "version": version}
            for version, suites in versions_by_suite
        ],
    }


TRIXIE_TODAY = _payload(
    ("1.2.0-4.1", ["sid"]),
    ("1.2.0-4", ["forky"]),
    ("1.1.0-2", ["trixie"]),
    ("1.0.9-2", ["bullseye", "bookworm"]),
)


class TestVersionOrdering:
    def test_newer_upstream_wins(self):
        assert watch.compare_versions("1.2.0-4.1", "1.1.0-2") > 0

    def test_numeric_not_lexicographic(self):
        # "1.10.0" < "1.2.0" as strings; Debian says the opposite.
        assert watch.compare_versions("1.10.0-1", "1.2.0-1") > 0

    def test_revision_breaks_ties(self):
        assert watch.compare_versions("1.1.0-3", "1.1.0-2") > 0

    def test_epoch_dominates(self):
        assert watch.compare_versions("1:1.0.0-1", "99.0.0-1") > 0

    def test_tilde_sorts_before_the_release(self):
        assert watch.compare_versions("1.1.0~rc1-1", "1.1.0-1") < 0

    def test_equal_versions_compare_equal(self):
        assert watch.compare_versions("1.1.0-2", "1.1.0-2") == 0


class TestEvaluate:
    def test_current_trixie_needs_nothing(self):
        newest, action_needed = watch.evaluate(TRIXIE_TODAY)

        assert newest == "1.1.0-2"
        assert action_needed is False

    def test_a_newer_trixie_package_asks_for_a_release(self):
        payload = _payload(
            ("1.2.1-1", ["trixie"]),
            ("1.2.0-4.1", ["sid"]),
        )
        newest, action_needed = watch.evaluate(payload)

        assert newest == "1.2.1-1"
        assert action_needed is True

    def test_versions_from_other_suites_do_not_count(self):
        # sid has 1.2.0; the image is built from trixie, so that is not a fix
        # for this deployment and must not trigger a release.
        newest, action_needed = watch.evaluate(TRIXIE_TODAY)

        assert newest == "1.1.0-2"
        assert action_needed is False

    def test_the_newest_of_several_suite_versions_wins(self):
        payload = _payload(("1.1.0-2", ["trixie"]), ("1.3.0-1", ["trixie"]))
        assert watch.newest_in_suite(payload) == "1.3.0-1"

    def test_a_suite_without_the_package_is_an_error_not_a_pass(self):
        payload = _payload(("1.2.0-4.1", ["sid"]))
        try:
            watch.evaluate(payload)
        except ValueError as exc:
            assert "trixie" in str(exc)
        else:  # pragma: no cover - the raise above is the expectation
            raise AssertionError("a missing suite must not be reported as clean")


class TestCli:
    def test_exit_0_when_trixie_is_unchanged(self, tmp_path):
        listing = tmp_path / "listing.json"
        listing.write_text(json.dumps(TRIXIE_TODAY), encoding="utf-8")

        assert watch.main(["--json-file", str(listing)]) == 0

    def test_exit_10_names_the_new_version(self, tmp_path, capsys):
        listing = tmp_path / "listing.json"
        listing.write_text(
            json.dumps(_payload(("1.2.1-1", ["trixie"]))), encoding="utf-8"
        )

        assert watch.main(["--json-file", str(listing)]) == 10
        output = capsys.readouterr().out
        assert "1.2.1-1" in output
        assert "a.1.0 release" in output

    def test_a_broken_listing_is_its_own_failure(self, tmp_path):
        listing = tmp_path / "listing.json"
        listing.write_text("{not json", encoding="utf-8")

        # 2, not 0: "could not check" must never read as "nothing to do".
        assert watch.main(["--json-file", str(listing)]) == 2

    def test_a_missing_file_is_its_own_failure(self, tmp_path):
        assert watch.main(["--json-file", str(tmp_path / "absent.json")]) == 2
