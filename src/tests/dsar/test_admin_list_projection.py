"""Admin DSAR list must project only the columns it serializes.

``get_admin_requests`` used ``SELECT * FROM dsar_requests``. The admin
data-export list then serialised each row through ``_serialize_request``,
which emits only ``REQUEST_RESPONSE_COLUMNS`` (12 fields). Every page thus
dragged ``storage_path`` (server filesystem/object layout), ``checksum`` and
``metadata`` across the DB boundary for every row, only to discard them.

The list path now threads the response allowlist into ``get_admin_requests``
as a SQL projection. These tests pin that projection at the SQL boundary and
assert the query count/width stay constant as the page grows. A static guard
keeps the route wired to the same allowlist (the route module's import chain
is not needed to exercise the core projection).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

ALLOWLIST = (
    "id",
    "user_id",
    "status",
    "requested_at",
    "completed_at",
    "expires_at",
    "format",
    "storage_backend",
    "file_size_bytes",
    "admin_id",
    "denial_reason",
    "error_message",
)

INTERNAL = ("storage_path", "checksum", "metadata")

_FULL_ROW = {col: 1 for col in ALLOWLIST}
_FULL_ROW.update(storage_path="/srv/exports/secret", checksum="deadbeef", metadata="{}")


class CountingDB:
    """Return only the columns named in the SELECT list and count them."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = []
        self.columns_read = 0

    def fetch_all(self, query, params=()):
        squashed = " ".join(query.split())
        self.queries.append((squashed, tuple(params)))
        upper = squashed.upper()
        if "SELECT *" in upper:
            out = [dict(row) for row in self.rows]
        else:
            select = squashed[
                upper.index("SELECT") + len("SELECT") : upper.index(" FROM")
            ]
            cols = [c.strip() for c in select.split(",")]
            out = [{c: row[c] for c in cols} for row in self.rows]
        self.columns_read += sum(len(row) for row in out)
        return out


def _core_listing(n, status=None):
    import src.core.dsar as dsar_mod
    from src.core.dsar.manager import DSARManager

    rows = [dict(_FULL_ROW, id=i, user_id=i + 1, admin_id=i + 2) for i in range(n)]
    db = CountingDB(rows)
    with (
        patch.object(dsar_mod, "_module_manager", DSARManager(db)),
        patch.object(dsar_mod, "_setup_complete", True),
    ):
        result = dsar_mod.get_admin_requests(status=status, limit=n, columns=ALLOWLIST)
    return result, db


def test_list_query_projects_only_allowlisted_columns():
    rows, db = _core_listing(3)

    assert len(db.queries) == 1
    sql, _params = db.queries[0]
    assert "SELECT *" not in sql
    for internal in INTERNAL:
        assert internal not in sql
    for col in ALLOWLIST:
        assert col in sql
    # Exactly the allowlisted columns were read, no internal ones.
    assert db.columns_read == 3 * len(ALLOWLIST)
    assert all(set(row) == set(ALLOWLIST) for row in rows)


def test_list_query_count_and_width_do_not_grow_with_page():
    _small, small_db = _core_listing(2)
    _large, large_db = _core_listing(50)

    assert len(small_db.queries) == len(large_db.queries) == 1
    assert small_db.columns_read == 2 * len(ALLOWLIST)
    assert large_db.columns_read == 50 * len(ALLOWLIST)


def test_projection_honours_status_filter_and_pagination():
    _rows, db = _core_listing(5, status="pending")

    sql, params = db.queries[0]
    assert "WHERE status = ?" in sql
    assert params == ("pending", 5, 0)


def test_route_threads_response_allowlist_into_listing():
    route_src = (
        Path(__file__).parents[3] / "src/api/routes/admin/data_export.py"
    ).read_text()
    start = route_src.index("async def list_data_exports")
    end = route_src.index("@router.get", start)
    body = route_src[start:end]
    assert "get_admin_requests(" in body
    assert "columns=REQUEST_RESPONSE_COLUMNS" in body
