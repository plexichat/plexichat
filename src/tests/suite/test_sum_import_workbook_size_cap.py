"""A bounded CSV import must not publish a workbook that cannot be decoded.

The storage container is zstd-compressed on write but decompressed under a
hard ceiling (``MAX_DECOMPRESSED_SIZE``) on every read.  The chunked import
path was the only writer that could add ~1M cells in a single request, so a
2 MB CSV -- comfortably inside the documented ``MAX_CSV_IMPORT_BYTES``,
``MAX_CSV_ROWS`` and ``MAX_CSV_COLUMNS`` limits -- could inflate the raw
SQLite blob past that ceiling.  The import was acknowledged and the revision
bumped, but every subsequent read (download, export, viewport) then failed
with "Decompressed container exceeds size limit": a permanent, unrecoverable
loss of the workbook.  An edit grantee could use this to destroy a shared
workbook.

The fix refuses to publish an oversized blob, so the revision is not bumped
and the previous, readable blob stays authoritative.
"""

import pytest

from src.core.suite.sum.container import (
    MAX_DECOMPRESSED_SIZE,
    decode_container,
    encode_container,
)
from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables
from src.core.suite.sum._shared import SumValidationError

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _workbook(manager, owner, title="import size cap"):
    wb = manager.create_workbook(owner.id, title)
    return int(wb["id"]), int(wb["sheets"][0]["id"])


def test_encode_container_refuses_a_payload_it_could_not_decode():
    """Symmetric guard at the format choke point: never wrap an undecodable blob."""
    oversized = b"x" * (MAX_DECOMPRESSED_SIZE + 1)
    with pytest.raises(ValueError, match="exceeds size limit"):
        encode_container(oversized)
    # A payload at the ceiling must still encode/decode intact (non-vacuous).
    at_limit = b"SQLite format 3\x00" + b"y" * (
        MAX_DECOMPRESSED_SIZE - len(b"SQLite format 3\x00")
    )
    db_bytes, _, meta = decode_container(encode_container(at_limit))
    assert len(db_bytes) == MAX_DECOMPRESSED_SIZE
    assert meta["uncompressed_size"] == MAX_DECOMPRESSED_SIZE


# This test builds and imports a 1,000,000-cell workbook (~15s on an idle box,
# several times that when four suite workers share the CPU). The default 60s
# budget measured the machine's queue, not the guard under test.
@pytest.mark.timeout(300)
def test_chunked_import_cannot_publish_unreadable_workbook(
    db, two_users, server_manager
):
    """A 2 MB CSV that would inflate past the decode cap is refused, not published."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    before = manager.get_workbook(owner.id, wb_id)
    blob_before = db.fetch_one(
        "SELECT blob_path, checksum, current_revision, blob_revision "
        "FROM sum_workbooks WHERE id = ?",
        (wb_id,),
    )

    # 10,000 rows x 100 columns of one-char values == exactly 2,000,000 bytes:
    # within every documented import bound, and 1,000,000 cells.  Measured,
    # this produces a ~65 MiB raw SQLite blob -- just past the 64 MiB ceiling.
    row = ",".join(["x"] * 100)
    content = "\n".join([row] * 10_000)
    assert len(content.encode("utf-8")) <= 2_000_000

    with pytest.raises(SumValidationError, match="workbook size limit"):
        manager.import_csv(
            owner.id,
            wb_id,
            sheet_id,
            content,
            base_revision=int(before["current_revision"]),
        )

    # Fail closed: no revision bump, the previous blob is still authoritative,
    # and the workbook still opens and decodes.
    after = manager.get_workbook(owner.id, wb_id)
    assert int(after["current_revision"]) == int(before["current_revision"])
    blob_after = db.fetch_one(
        "SELECT blob_path, checksum, current_revision, blob_revision "
        "FROM sum_workbooks WHERE id = ?",
        (wb_id,),
    )
    assert dict(blob_after) == dict(blob_before)
    row_meta = db.fetch_one(
        "SELECT blob_path FROM sum_workbooks WHERE id = ?", (wb_id,)
    )
    container = manager._blob.retrieve(row_meta["blob_path"])
    db_bytes, _, _ = decode_container(container)
    assert len(db_bytes) <= MAX_DECOMPRESSED_SIZE


def test_ordinary_import_still_lands_after_the_guard(db, two_users, server_manager):
    """Non-vacuous control: a legitimate bounded import is unaffected."""
    owner, _ = two_users
    manager = _manager(db, server_manager)
    wb_id, sheet_id = _workbook(manager, owner)
    revision = int(manager.get_workbook(owner.id, wb_id)["current_revision"])

    content = "\n".join(",".join(f"r{r}c{c}" for c in range(100)) for r in range(30))
    result = manager.import_csv(
        owner.id, wb_id, sheet_id, content, base_revision=revision
    )
    assert result["cells"] == 3000
    viewport = manager.read_viewport(owner.id, wb_id, sheet_id, 0, 2, 0, 2)
    assert viewport["cells"][0]["value"] == "r0c0"
