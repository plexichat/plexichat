"""Query-count regressions for the admin approval gate.

``require_approved_request`` used to issue separate ``SELECT COUNT(*)`` and
``SELECT id FROM admin_approvals`` statements before consuming the approval.
Both reads are now folded into one scalar-subquery statement; the atomic
single-use consume UPDATE still runs on its own. These tests pin the query
count while confirming the security decisions are unchanged.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.api.routes.admin import utils as admin_utils


class _Cursor:
    rowcount = 1


class _CountingDB:
    def __init__(self, row, fail_read=False):
        self.row = row
        self.fail_read = fail_read
        self.fetches = []
        self.executes = []

    def fetch_one(self, query, params=()):
        self.fetches.append((query, tuple(params)))
        if self.fail_read:
            raise RuntimeError("database read failed")
        return self.row

    def execute(self, query, params=()):
        self.executes.append((query, tuple(params)))
        return _Cursor()


def _config(monkeypatch, single_admin_bypass=True):
    monkeypatch.setattr(
        admin_utils.config,
        "get",
        lambda key, default=None: (
            {"approval_workflows": {"single_admin_bypass": single_admin_bypass}}
            if key == "admin_ui"
            else default
        ),
    )


@pytest.mark.unit
def test_gate_reads_admin_count_and_approval_in_one_query(monkeypatch):
    _config(monkeypatch)
    db = _CountingDB({"admin_count": 3, "approval_id": 42})
    with patch("src.api.get_db", return_value=db):
        assert admin_utils.require_approved_request("migrations.run") == 42

    assert len(db.fetches) == 1
    read = db.fetches[0][0].lower()
    assert "count(*)" in read and "approval_id" in read
    # The approval consume is still a separate, single-use UPDATE.
    assert len(db.executes) == 1
    assert "status = 'consumed'" in db.executes[0][0]


@pytest.mark.unit
def test_gate_handles_tuple_rows(monkeypatch):
    """Real DB adapters that return tuples are supported as before."""
    _config(monkeypatch)
    db = _CountingDB((3, 9))
    with patch("src.api.get_db", return_value=db):
        assert admin_utils.require_approved_request("search.reindex") == 9


@pytest.mark.unit
def test_gate_fails_closed_when_the_batched_read_fails(monkeypatch):
    _config(monkeypatch)
    db = _CountingDB(None, fail_read=True)
    with patch("src.api.get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            admin_utils.require_approved_request("migrations.rollback")
    assert exc.value.status_code == 403
    assert not db.executes


@pytest.mark.unit
def test_bypass_disabled_never_grants_access_on_single_admin(monkeypatch):
    _config(monkeypatch, single_admin_bypass=False)
    db = _CountingDB({"admin_count": 1, "approval_id": None})
    with patch("src.api.get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            admin_utils.require_approved_request("migrations.run")
    assert exc.value.status_code == 403
