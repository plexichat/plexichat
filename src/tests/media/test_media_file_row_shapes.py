"""Tuple-row adapters for the media-file read path.

Admin database adapters may return mapping or tuple rows (see
docs/admin-hardening.md). These tests pin the tuple shape for
``get_file``, ``get_file_by_filename``, ``get_files_by_filenames``,
``check_file_access``, and the infected-file quarantine in ``_scanning``.

Run with: pytest src/tests/media/test_media_file_row_shapes.py -q
"""

import pytest


# A ``media_files`` row in schema order (see ``src/core/media/schema.py``).
_FILE_ROW = (
    101,  # id
    "abc123.png",  # filename
    "orig.png",  # original_filename
    "image/png",  # content_type
    12,  # size
    "image",  # media_type
    "local",  # storage_backend
    "image/2026/01/01/abc123.png",  # storage_path
    "deadbeef",  # checksum
    7,  # uploaded_by
    1700000000,  # uploaded_at
    '{"x": 1}',  # metadata
    "clean",  # scan_status
    None,  # scan_result
    0,  # deleted
    None,  # deleted_at
)


class _StubStorage:
    def get_url(self, path):
        return f"/media/{path}"

    def delete(self, path):
        return True


class _TupleFileDB:
    """Bare-tuple rows, as a raw cursor returns them."""

    def __init__(self, one=None, all_rows=None):
        self._one = one
        self._all_rows = list(all_rows or [])
        self.executed = []

    def fetch_one(self, query, params=()):
        return self._one

    def fetch_all(self, query, params=()):
        return list(self._all_rows)

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


def _make_manager(db):
    from src.core.media.manager import MediaManager

    manager = MediaManager.__new__(MediaManager)
    manager._db = db
    manager._messaging = None
    manager._config = {"storage_backend": "local"}
    manager._storage = _StubStorage()
    manager._db_storage = None
    return manager


@pytest.mark.media
class TestMediaFileTupleRows:
    def test_get_file_maps_tuple_row(self):
        manager = _make_manager(_TupleFileDB(one=_FILE_ROW))

        file = manager.get_file(101)

        assert file is not None
        assert file.id == 101
        assert file.filename == "abc123.png"
        assert file.original_filename == "orig.png"
        assert file.content_type == "image/png"
        assert file.size == 12
        assert file.storage_path == "image/2026/01/01/abc123.png"
        assert file.checksum == "deadbeef"
        assert file.uploaded_by == 7
        assert file.metadata == {"x": 1}
        assert file.deleted is False

    def test_get_file_by_filename_maps_tuple_row(self):
        manager = _make_manager(_TupleFileDB(one=_FILE_ROW))

        file = manager.get_file_by_filename("abc123.png")

        assert file is not None
        assert file.id == 101
        assert file.filename == "abc123.png"

    def test_get_files_by_filenames_keys_by_filename(self):
        manager = _make_manager(_TupleFileDB(all_rows=[_FILE_ROW]))

        result = manager.get_files_by_filenames(["abc123.png"])

        assert list(result) == ["abc123.png"]
        assert result["abc123.png"].id == 101

    def test_check_file_access_uploader_tuple_row(self):
        # ``SELECT id, uploaded_by, checksum`` column order.
        manager = _make_manager(_TupleFileDB(one=(101, 7, "deadbeef")))

        assert manager.check_file_access("abc123.png", 7) is True

    def test_check_file_access_missing_file_tuple_db(self):
        manager = _make_manager(_TupleFileDB(one=None))

        assert manager.check_file_access("gone.png", 7) is False

    def test_handle_infected_file_tuple_rows(self):
        db = _TupleFileDB(
            one=("abc123.png",),
            all_rows=[(55, 9001, '{"file_id": 101}')],
        )
        manager = _make_manager(db)

        manager._handle_infected_file(101, 7)

        soft_deletes = [
            params
            for query, params in db.executed
            if "msg_attachments SET deleted = 1" in query
        ]
        assert soft_deletes == [(55,)]
