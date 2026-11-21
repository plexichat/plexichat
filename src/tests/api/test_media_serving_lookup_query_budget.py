"""Attachment serving must read stable metadata and scan state in one query.

The serve gate used to issue TWO statements on a cold lookup: a filename query
for the stable row (memoised in ``_media_metadata_cache``) and then a second
by-id query for ``scan_status`` / ``deleted``. Both read the same
``media_files`` row, so the second round trip was pure overhead on the first
request for each file.

``_load_servable_metadata`` now selects ``scan_status`` in the first statement
and reuses it for that request. A cache hit still issues the fresh by-id read,
so the fail-closed scan/deleted gate is unchanged. These tests pin both the
query-count win and the security invariants with a counting DB wrapper.
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import patch

import pytest

from src.api.media_serving import _load_servable_metadata, _media_metadata_cache
from src.utils import encryption

pytestmark = [pytest.mark.unit]


_USER_ID = 4242
_FILENAME = "budget-file.txt"


class _CountingDB:
    """Delegate to the real DB while counting reads and capturing SQL."""

    def __init__(self, db):
        object.__setattr__(self, "db", db)
        object.__setattr__(self, "reads", [])
        object.__setattr__(self, "sql", [])

    def __getattr__(self, name):
        attr = getattr(self.db, name)

        def wrapped(*args, **kwargs):
            if name in ("fetch_one", "fetch_all"):
                object.__setattr__(
                    self, "reads", self.reads + [(name, args[0] if args else "")]
                )
                object.__setattr__(self, "sql", self.sql + [args[0] if args else ""])
            return attr(*args, **kwargs)

        return wrapped


@pytest.fixture(autouse=True)
def _clear_metadata_cache():
    _media_metadata_cache.clear()
    yield
    _media_metadata_cache.clear()


def _seed(db, scan_status="clean", deleted=0) -> None:
    now = int(time.time() * 1000)
    db.execute(
        "INSERT INTO media_files (id, uploaded_by, filename, original_filename, "
        "content_type, size, media_type, storage_backend, storage_path, "
        "checksum, uploaded_at, scan_status, scan_result, deleted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            900_001,
            _USER_ID,
            _FILENAME,
            _FILENAME,
            "text/plain",
            16,
            "document",
            "local",
            f"uploads/{_USER_ID}/{_FILENAME}",
            "checksum-budget",
            now,
            scan_status,
            None,
            deleted,
        ),
    )


def test_cold_lookup_uses_one_select_for_metadata_and_scan(db):
    _seed(db, scan_status="clean")

    counting = _CountingDB(db)
    row, scan_row = _load_servable_metadata(counting, _FILENAME)

    assert row is not None
    assert row["id"] == 900_001
    assert scan_row == {"scan_status": "clean", "deleted": 0}
    # Before the fix a cold lookup issued two SELECTs on media_files.
    assert len(counting.reads) == 1, counting.reads
    assert counting.reads[0][0] == "fetch_one"
    # The one statement carries the scan state and fails closed on deleted.
    assert "scan_status" in counting.sql[0]
    assert "deleted = 0" in counting.sql[0]


def test_warm_lookup_still_rechecks_scan_state_freshly(db):
    _seed(db, scan_status="clean")

    # First lookup warms the metadata LRU.
    first = _CountingDB(db)
    _load_servable_metadata(first, _FILENAME)
    assert len(first.reads) == 1

    # The scanner quarantines the file after the metadata was cached.
    db.execute("UPDATE media_files SET scan_status = 'pending' WHERE id = 900_001")

    second = _CountingDB(db)
    row, scan_row = _load_servable_metadata(second, _FILENAME)

    assert row is not None
    # The stale cached row must not decide servability: a fresh by-id read ran.
    assert scan_row["scan_status"] == "pending"
    assert len(second.reads) == 1, second.reads
    assert "WHERE id = ?" in second.sql[0]
    assert "scan_status" in second.sql[0]


def test_deleted_file_is_not_servable(db):
    _seed(db, scan_status="clean", deleted=1)

    counting = _CountingDB(db)
    row, scan_row = _load_servable_metadata(counting, _FILENAME)

    assert (row, scan_row) == (None, None)
    assert len(counting.reads) == 1


def test_before_after_query_counts(db):
    """Explicit before/after measurement: two reads become one on a cold path."""
    _seed(db, scan_status="clean")

    # "Before": the original two-statement cold path.
    before = _CountingDB(db)
    cached = before.fetch_one(
        "SELECT id, storage_backend, storage_path, content_type, original_filename "
        "FROM media_files WHERE filename = ? AND deleted = 0",
        (_FILENAME,),
    )
    before.fetch_one(
        "SELECT scan_status, deleted FROM media_files WHERE id = ?", (cached["id"],)
    )
    before_count = len(before.reads)

    # "After": the folded single-statement cold path.
    after = _CountingDB(db)
    _load_servable_metadata(after, _FILENAME)
    after_count = len(after.reads)

    assert before_count == 2
    assert after_count == 1
    assert after_count < before_count


@pytest.fixture
def attachment_media_dir(tmp_path):
    """Point media storage at a per-test temp dir before ``test_client`` boots."""
    import utils.config as config

    assert config._config_instance is not None
    upload_dir = tmp_path / "uploads"
    config._config_instance.config["media"] = {
        "storage_backend": "local",
        "local_path": str(upload_dir),
        "local_url": "/media",
        "compression": {"enabled": False},
        "signing_key": "query-budget-signing-key",
        "signing_expiry": 3600,
        "scanner_enabled": False,
        "proxy_enabled": False,
    }
    getattr(config._config_instance, "_invalidate_cache")()
    return upload_dir


def test_serve_route_uses_the_folded_lookup(
    attachment_media_dir, test_client, db, media_manager, auth_manager
):
    """The real route must issue a single media_files SELECT on a cold serve."""
    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"qb_{unique}",
            email=f"qb_{unique}@test.local",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        token_result = auth_manager.login(f"qb_{unique}", "TestPass123!")

    uploaded = media_manager.upload_file(
        user_id=user.id,
        file_data=b"query-budget-bytes",
        filename="qb.txt",
        content_type="text/plain",
    )
    row = db.fetch_one(
        "SELECT filename, scan_status FROM media_files WHERE id = ?",
        (uploaded.file_id,),
    )
    if row and row["scan_status"] == "pending":
        db.execute(
            "UPDATE media_files SET scan_status = 'clean' WHERE id = ?",
            (uploaded.file_id,),
        )
    filename = row["filename"]

    signed = media_manager.sign_url(
        uploaded.file_id,
        user_id=user.id,
        url=f"/api/v1/media/attachments/{filename}",
        expires_in=600,
    )

    counting = _CountingDB(db)
    with patch("src.api.get_db", return_value=counting):
        resp = test_client.get(
            signed.url, headers={"Authorization": f"Bearer {token_result.token}"}
        )

    assert resp.status_code == 200, resp.text
    media_selects = [sql for _, sql in counting.reads if "media_files" in sql]
    # Before the fix the cold serve issued two media_files SELECTs here.
    assert len(media_selects) == 1, media_selects
    assert "scan_status" in media_selects[0]
