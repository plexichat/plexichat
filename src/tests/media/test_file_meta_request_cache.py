"""Request-scoped dedup of media-file identity lookups.

Resolving a media file and then authorising access used to query
``media_files`` for the same ``id``/``uploaded_by``/``checksum`` triple several
times in one request (stash/grants, attachment serving, thumbnail checks).
``FileMetaCache`` memoises only those stable fields: scan_status and deleted
are intentionally never cached and every upload/delete path invalidates.

These tests measure the query reduction, pin the bounded-cache invariant and
verify that invalidation cannot leave a stale access grant.

Run with: pytest src/tests/media/test_file_meta_request_cache.py -q
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.media._file_meta_cache import FileMeta, FileMetaCache
from src.core.media.models import ScanStatus

# Minimal valid PNG (1x1 pixel).
MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _stored_name(db, file_id):
    row = db.fetch_one("SELECT filename FROM media_files WHERE id = ?", (file_id,))
    return row["filename"]


def _upload(manager, user_id, name):
    return manager.upload_file(
        user_id=user_id,
        file_data=MINI_PNG,
        filename=name,
        content_type="image/png",
    )


@pytest.mark.media
def test_repeated_identity_lookups_collapse_in_one_request(
    media_manager, db, test_user
):
    """3 repeated identity reads cost 1 query instead of 4 (75% reduction)."""
    result = _upload(media_manager, test_user.id, "meta_cache_request.png")
    stored = _stored_name(db, result.file_id)

    cache = media_manager._file_meta_cache
    cache.clear()

    original_fetch_one = db.fetch_one
    thumbnail = None

    # ── "Before": no memoisation between the three reads of the same file ──
    # Clearing before each read reproduces the pre-cache DB cost exactly.
    with patch.object(db, "fetch_one", side_effect=original_fetch_one) as spy:
        cache.clear()
        file = media_manager.get_file_by_filename(stored)
        assert file is not None
        before_resolve = spy.call_count
        spy.reset_mock()

        cache.clear()
        assert media_manager.check_file_access(stored, test_user.id) is True
        before_direct = spy.call_count
        spy.reset_mock()

        thumbnail = f"thumbnails/{file.id}/{stored}"
        cache.clear()
        assert media_manager.check_file_access(thumbnail, test_user.id) is True
        before_thumbnail = spy.call_count

    before = before_resolve + before_direct + before_thumbnail

    # ── "After": one resolve primes the cache for the rest of the request ──
    with patch.object(db, "fetch_one", side_effect=original_fetch_one) as spy:
        cache.clear()
        file = media_manager.get_file_by_filename(stored)
        assert file is not None
        after_resolve = spy.call_count

        spy.reset_mock()
        assert media_manager.check_file_access(stored, test_user.id) is True
        after_direct = spy.call_count

        spy.reset_mock()
        assert media_manager.check_file_access(thumbnail, test_user.id) is True
        after_thumbnail = spy.call_count

    after = after_resolve + after_direct + after_thumbnail

    # before = 1 resolve + 1 direct + 2 thumbnail (parent filename + recursive)
    assert (before_resolve, before_direct, before_thumbnail) == (1, 1, 2)
    assert (after_resolve, after_direct, after_thumbnail) == (1, 0, 0)
    assert after == 1 and before == 4
    assert after < before
    assert cache.stats()["hits"] >= 2


@pytest.mark.media
def test_cold_access_check_still_queries_once(media_manager, db, test_user):
    """A cache miss is not silently granted; the DB remains authoritative."""
    result = _upload(media_manager, test_user.id, "meta_cache_cold.png")
    stored = _stored_name(db, result.file_id)
    media_manager._file_meta_cache.clear()

    original_fetch_one = db.fetch_one
    with patch.object(db, "fetch_one", side_effect=original_fetch_one) as spy:
        assert media_manager.check_file_access(stored, test_user.id) is True
        assert spy.call_count == 1


@pytest.mark.media
def test_upload_primes_cache_and_delete_invalidates(media_manager, db, test_user):
    """Upload needs no follow-up lookup; delete drops both cache keys."""
    cache = media_manager._file_meta_cache
    cache.clear()

    result = _upload(media_manager, test_user.id, "meta_cache_invalidate.png")
    stored = _stored_name(db, result.file_id)
    file_id = result.file_id

    # Primed by the INSERT: access check is free even with no prior read.
    original_fetch_one = db.fetch_one
    with patch.object(db, "fetch_one", side_effect=original_fetch_one) as spy:
        assert media_manager.check_file_access(stored, test_user.id) is True
        assert spy.call_count == 0

    assert cache.get_by_filename(stored) is not None
    assert cache.get_by_id(int(file_id)) is not None

    assert media_manager.delete_file(test_user.id, file_id) is True
    assert cache.get_by_filename(stored) is None
    assert cache.get_by_id(int(file_id)) is None

    # The next check must hit the DB and be denied for the soft-deleted file.
    with patch.object(db, "fetch_one", side_effect=original_fetch_one) as spy:
        assert media_manager.check_file_access(stored, test_user.id) is False
        assert spy.call_count >= 1


@pytest.mark.media
def test_scan_status_is_never_served_from_cache(media_manager, db, test_user):
    """Only stable fields are cached; mutable scan_status stays fresh."""
    result = _upload(media_manager, test_user.id, "meta_cache_scan.png")
    stored = _stored_name(db, result.file_id)

    # Prime the cache while the row is still PENDING.
    assert media_manager.get_file_by_filename(stored) is not None
    db.execute(
        "UPDATE media_files SET scan_status = 'clean' WHERE id = ?",
        (result.file_id,),
    )

    assert media_manager.get_file(result.file_id).scan_status is ScanStatus.CLEAN
    assert media_manager.get_file_by_filename(stored).scan_status is ScanStatus.CLEAN


@pytest.mark.media
def test_file_meta_cache_is_bounded():
    """The cache cannot grow past ``maxsize`` keys and reports evictions."""
    cache = FileMetaCache(maxsize=8)
    for i in range(50):
        cache.put(FileMeta(id=i, filename=f"f{i}.png", uploaded_by=1))

    assert len(cache) <= 8
    assert cache.stats()["evictions"] > 0
    assert cache.get_by_id(0) is None  # oldest entry was evicted

    cache.invalidate(file_id=49)
    assert cache.get_by_filename("f49.png") is None
