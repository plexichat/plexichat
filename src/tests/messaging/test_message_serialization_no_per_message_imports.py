"""Serialization hot path must not run import statements per message.

``_message_to_response`` is executed once for every message on a list/search/
pins page (up to 100 per request). It used to issue ``IMPORT_NAME`` bytecode for
``ReaderInfo`` and ``USER_MENTION_PATTERN`` on *every* call, and re-import
``json`` for every message carrying string metadata. Python resolves imports
through ``sys.modules`` and the import machinery each time, so a 100-message
page paid ~200 redundant import dispatches. The symbols are now bound at module
import time; serialization does zero imports per message.

The probe patches ``builtins.__import__`` (the exact hook ``IMPORT_NAME`` calls)
and counts dispatches, so the assertion is deterministic rather than a flaky
wall-clock timing check.
"""

import builtins
import json

import pytest

from src.api.routes.messages.helpers import _message_to_response


def _message(i, *, string_metadata=False, string_att_metadata=False):
    attachments = []
    if string_att_metadata:
        attachments = [
            {
                "id": 5000 + i,
                "filename": "a.png",
                "url": "https://example.com/a.png",
                "content_type": "image/png",
                "size": 1,
                "metadata": json.dumps({"file_id": 9}),
            }
        ]
    meta = {"k": "v", "n": i}
    return {
        "id": 1000 + i,
        "author_id": 7,
        "content": f"hi <@{100 + i}> and <@{200 + i}>",
        "created_at": 1_700_000_000_000 + i,
        "attachments": attachments,
        "embeds": [],
        "channel_id": 55,
        "metadata": json.dumps(meta) if string_metadata else meta,
    }


_READ_BY = [{"id": 42, "username": "reader", "avatar_url": None}]


def _serialize(messages):
    for m in messages:
        _message_to_response(
            m,
            suppress_embeds=False,
            read_by_data=_READ_BY,
        )


def _count_imports(messages):
    # Warm up so first-time model/schema construction is not attributed to the
    # per-message path we are measuring.
    _serialize([_message(-1, string_metadata=True, string_att_metadata=True)])

    real_import = builtins.__import__
    counter = {"n": 0}

    def counting_import(name, globals=None, locals=None, fromlist=(), level=0):
        counter["n"] += 1
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = counting_import
    try:
        _serialize(messages)
    finally:
        builtins.__import__ = real_import
    return counter["n"]


@pytest.mark.messaging
def test_serialization_runs_no_imports_per_message():
    # Before the fix: 2 imports/message (ReaderInfo + USER_MENTION_PATTERN),
    # plus one more per message with string metadata. A 100-message page cost
    # ~200 import dispatches. After: zero, independent of page size.
    small = _count_imports([_message(i) for i in range(10)])
    large = _count_imports([_message(i) for i in range(100)])
    mixed = _count_imports(
        [
            _message(i, string_metadata=True, string_att_metadata=True)
            for i in range(100)
        ]
    )
    assert small == 0
    assert large == 0
    assert mixed == 0


@pytest.mark.messaging
def test_hoisted_imports_keep_serialization_semantics():
    """Moving imports to module scope must not change any output field."""
    resp = _message_to_response(
        _message(3, string_metadata=True, string_att_metadata=True),
        suppress_embeds=False,
        read_by_data=_READ_BY,
    )
    # USER_MENTION_PATTERN still parsed from content.
    assert resp.mentions == ["103", "203"]
    # string metadata still decoded into a dict.
    assert resp.metadata == {"k": "v", "n": 3}
    # ReaderInfo still constructed for read receipts.
    assert len(resp.read_by) == 1
    assert resp.read_by[0].username == "reader"
    # string attachment metadata (file_id) still decoded.
    assert resp.attachments[0].url.startswith("https://example.com/")
