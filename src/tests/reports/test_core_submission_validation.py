"""Regression tests for core-level report submission validation.

The HTTP routes validate category and evidence ids, but ``report_message`` /
``report_user`` are also public core helpers (see the module README and
internal callers) invoked directly. Two gaps were closed at the store boundary:

* ``category`` was accepted verbatim despite the closed ``ReportCategory``
  enum, letting arbitrary markup / control strings persist in a moderator
  field (category enum bypass).
* ``evidence_message_ids`` was ``",".join(str(x) ...)``-ed without validation
  and parsed back with ``int()`` on read, so injected commas, control
  characters or junk corrupted the column or crashed moderators (evidence
  injection).

These tests exercise the core helpers directly so a schema/route change cannot
silently reintroduce either weakness.
"""

import pytest

from src.core import reports
from src.core.reports import (
    MAX_EVIDENCE_IDS,
    MAX_SNOWFLAKE_ID,
    ReportCategory,
    _validated_category,
    _validated_evidence_ids,
)


class _Result:
    rowcount = 1


class _FakeDB:
    """Minimal DB stub capturing writes without needing a live database."""

    def __init__(self):
        self.inserts = []

    def execute(self, sql, params=None):
        self.inserts.append((sql, params))
        return _Result()

    def fetch_one(self, sql, params=None):
        return None


@pytest.fixture
def core_reports(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(reports, "_db", db)
    monkeypatch.setattr(reports, "_setup_complete", True)
    monkeypatch.setattr(reports, "_messaging", None)
    return reports, db


# ---------------------------------------------------------------------------
# Category enum bypass
# ---------------------------------------------------------------------------


def test_validated_category_accepts_every_enum_value():
    for value in _VALID_VALUES:
        assert _validated_category(value) == value


_VALID_VALUES = frozenset(c.value for c in ReportCategory)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "bogus",
        "HARASSMENT",  # enum values are lower-case
        "<script>alert(1)</script>",
        "spam\ninjected",
        "spam\x00",
        None,
        123,
        ["spam"],
    ],
)
def test_validated_category_rejects_unknown_values(bad):
    with pytest.raises(ValueError):
        _validated_category(bad)


def test_report_message_rejects_invalid_category_before_insert(core_reports):
    reports, db = core_reports
    with pytest.raises(ValueError):
        reports.report_message(1, 2, 3, "reason", category="bogus")
    assert db.inserts == []


def test_report_user_rejects_invalid_category_before_insert(core_reports):
    reports, db = core_reports
    with pytest.raises(ValueError):
        reports.report_user(1, 2, "reason", category="<b>injected</b>")
    assert db.inserts == []


# ---------------------------------------------------------------------------
# Evidence injection
# ---------------------------------------------------------------------------


def test_validated_evidence_ids_normalises_valid_inputs():
    assert _validated_evidence_ids(None) is None
    assert _validated_evidence_ids([50, 60]) == [50, 60]
    assert _validated_evidence_ids(["50", "60"]) == [50, 60]
    assert _validated_evidence_ids([]) == []


@pytest.mark.parametrize(
    "bad",
    [
        ["1,2"],            # comma injection -> extra ids on read
        ["1\n2"],           # control character / log forging
        ["123\x00"],        # NUL byte
        ["1<script>2"],     # markup
        [" 123"],           # whitespace
        ["１２３"],          # non-ASCII digit look-alikes
        ["-1"],             # negative
        [0],                # zero
        [-5],
        [MAX_SNOWFLAKE_ID + 1],
        [True],             # bool is an int subclass
        [1.5],              # float
        [["1"]],            # nested
        "123",              # a bare string is not a list
    ],
)
def test_validated_evidence_ids_rejects_injected_or_out_of_range(bad):
    with pytest.raises(ValueError):
        _validated_evidence_ids(bad)


def test_validated_evidence_ids_caps_list_length():
    ok = [str(i + 1) for i in range(MAX_EVIDENCE_IDS)]
    assert _validated_evidence_ids(ok) == [int(i) for i in ok]
    with pytest.raises(ValueError):
        _validated_evidence_ids(ok + ["9999"])


def test_report_user_stores_canonical_evidence_and_returns_ints(core_reports):
    reports, db = core_reports
    report = reports.report_user(
        reporter_id=1,
        reported_user_id=2,
        reason="spam",
        evidence_message_ids=["50", 60],
    )
    assert report.evidence_message_ids == [50, 60]
    # The persisted column is a canonical, comma-free id string.
    params = db.inserts[0][1]
    assert params[6] == "50,60"


def test_report_user_rejects_evidence_injection_before_insert(core_reports):
    reports, db = core_reports
    with pytest.raises(ValueError):
        reports.report_user(1, 2, "spam", evidence_message_ids=["1,2"])
    assert db.inserts == []
