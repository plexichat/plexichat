"""Regressions for two PlexiSum security boundaries.

* CSV export must neutralise spreadsheet formula cells even when the dangerous
  lead is hidden behind leading whitespace (Excel/Sheets strip it before
  deciding a cell is a formula). Checking only the first character misses that
  bypass.
* Grant management is a sharing act, not an ordinary cell edit. An ``edit``
  grantee must not be able to create grants for strangers or revoke grants the
  owner created.
"""

import csv
import io

import pytest

from src.core.suite.sum.manager import SumManager, SumPermissionError
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _make_manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _write_cell(manager, user, workbook, sheet_id, value):
    manager.apply_operation(
        user.id,
        int(workbook["id"]),
        int(sheet_id),
        f"op-{workbook['id']}-{abs(hash(value))}",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "text", "value": value}],
    )


@pytest.mark.parametrize(
    "value",
    [
        "=1+1",
        "+1+1",
        "-1+1",
        "@SUM(1)",
        '=HYPERLINK("http://evil.test")',
        "  =1+1",  # leading spaces (the bypass a first-char check misses)
        "\t=1+1",  # leading tab
        "\r=1+1",  # leading CR
        " \t =1+1",  # mixed leading whitespace
    ],
)
def test_export_csv_neutralises_formula_leads_even_behind_whitespace(
    db, two_users, server_manager, value
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "CSV injection")
    sheet_id = workbook["sheets"][0]["id"]
    _write_cell(manager, owner, workbook, sheet_id, value)

    csv_text = manager.export_csv(owner.id, int(workbook["id"]), int(sheet_id))[
        "content"
    ]
    rows = list(csv.reader(io.StringIO(csv_text)))

    # The apostrophe prefix forces the spreadsheet to treat the cell as text.
    assert rows and rows[0], (value, csv_text)
    assert rows[0][0] == "'" + value, (value, rows)


def test_export_csv_leaves_plain_text_and_numbers_untouched(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "CSV plain")
    sheet_id = workbook["sheets"][0]["id"]
    manager.apply_operation(
        owner.id,
        int(workbook["id"]),
        int(sheet_id),
        "plain-op",
        "set_cell",
        0,
        [
            {"row": 0, "column": 0, "value_type": "text", "value": "hello world"},
            {"row": 0, "column": 1, "value_type": "number", "value": -5},
        ],
    )

    csv_text = manager.export_csv(owner.id, int(workbook["id"]), int(sheet_id))[
        "content"
    ]
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0] == ["hello world", "-5"]


def test_edit_grantee_cannot_grant_workbook(db, two_users, server_manager):
    owner, editor = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "ACL")
    wb_id = int(workbook["id"])
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])

    with pytest.raises(SumPermissionError):
        manager.grant_workbook(editor.id, wb_id, editor.id + 5000, ["edit"])


def test_edit_grantee_cannot_revoke_owner_grant(db, two_users, server_manager):
    owner, editor = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "ACL")
    wb_id = int(workbook["id"])
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    grant = manager.grant_workbook(owner.id, wb_id, editor.id + 5000, ["view"])

    with pytest.raises(SumPermissionError):
        manager.revoke_grant(editor.id, int(grant["id"]))

    # The owner still retains full share authority.
    manager.revoke_grant(owner.id, int(grant["id"]))
