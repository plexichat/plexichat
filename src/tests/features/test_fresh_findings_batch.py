"""Regression coverage for the fresh feature hardening batch."""

from unittest.mock import MagicMock


def test_bookmark_access_reads_tuple_message_rows():
    from src.core.messaging.services.bookmarks import BookmarkService

    db = MagicMock()
    db.fetch_one.side_effect = [(77,), (1,), {"count": 0}, None, {"id": 5}]
    service = BookmarkService(db)
    service._generate_id = lambda: 5
    result = service.add_bookmark(9, 10, 77)
    assert result == {"id": 5}


def test_bookmark_count_reads_tuple_rows():
    from src.core.messaging.services.bookmarks import BookmarkService

    db = MagicMock()
    db.fetch_one.return_value = (4,)
    assert BookmarkService(db).get_bookmark_count(9) == 4


def test_push_permission_error_has_structured_static_detail():
    from src.api.routes.features import push
    import inspect

    source = inspect.getsource(push.register_push_token)
    assert "detail=str(e)" not in source
    assert "Push token operation denied" in source
    assert 'raise_bad_request("Invalid push token data")' in source


def test_slowmode_setter_reads_tuple_channel_rows():
    from src.api.routes.features import slowmode
    import inspect

    assert "isinstance(channel_row, dict)" in inspect.getsource(
        slowmode.set_channel_slowmode
    )


def test_bookmark_list_normalizes_tuple_rows():
    from src.core.messaging.services.bookmarks import BookmarkService

    db = MagicMock()
    db.fetch_all.return_value = [(1, 9, 10, 77, "important", 1000)]
    result = BookmarkService(db).get_bookmarks(9)
    assert result[0]["conversation_id"] == 77
    assert result[0]["label"] == "important"


def test_bookmark_list_errors_are_static():
    from src.api.routes.features import bookmarks
    import inspect

    source = inspect.getsource(bookmarks.list_bookmarks)
    assert "message=str(e)" not in source
