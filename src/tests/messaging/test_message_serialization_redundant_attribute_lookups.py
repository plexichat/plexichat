"""Per-message serialization must read each message attribute at most once.

``_message_to_response`` runs once for every message on a list/search/pins page
(up to 100 per request).  It used to allocate a fresh ``get_attr`` closure for
every message even though an identical module-level ``_get_attr`` already
existed, and it re-read several fields multiple times:

* ``status``      -- 3 lookups/message (condition + getattr target + fallback)
* ``reply_to_id`` -- 2 lookups/message (the value and the truthiness guard)

On a 100-message page that is hundreds of redundant ``dict.get`` dispatches
plus 100 throwaway function objects.  The serializer now reuses the shared
accessor and resolves each of these fields into a local exactly once, while
producing byte-identical response fields.
"""

import pytest

from src.api.routes.messages.helpers import _message_to_response

pytestmark = [pytest.mark.messaging, pytest.mark.unit]


class _CountingDict(dict):
    """A message row that records how often each key is read via ``.get``."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_counts = {}

    def get(self, key, default=None):
        self.get_counts[key] = self.get_counts.get(key, 0) + 1
        return super().get(key, default)


class _Status:
    """An enum-like status object whose ``.value`` is the wire representation."""

    value = "read"


def _message():
    return _CountingDict(
        {
            "id": 1000,
            "author_id": 7,
            "content": "hello",
            "created_at": 1_700_000_000_000,
            "channel_id": 55,
            "status": _Status(),
            "reply_to_id": 999,
            "metadata": {"k": "v"},
            "edited": True,
            "edited_at": 1_700_000_001_000,
            "embeds": [],
        }
    )


@pytest.mark.messaging
def test_repeated_fields_are_read_once_per_message():
    msg = _message()
    resp = _message_to_response(msg, suppress_embeds=False)

    # Before the fix: status=3 and reply_to_id=2 lookups per message.
    assert msg.get_counts.get("status", 0) == 1
    assert msg.get_counts.get("reply_to_id", 0) == 1

    # And the serialized values are unchanged by collapsing the reads.
    assert resp.status == "read"
    assert str(resp.reply_to_id) == "999"
    assert resp.edited_at == 1_700_000_001_000
    assert resp.metadata == {"k": "v"}


@pytest.mark.messaging
def test_lookup_count_is_constant_per_message_across_a_page():
    """Serializing N messages must cost N * (fixed lookups), not more."""
    counts = []
    for i in range(20):
        msg = _message()
        msg["id"] = 1000 + i
        _message_to_response(msg, suppress_embeds=False)
        counts.append(sum(msg.get_counts.values()))

    # Every message is identical, so the cost per message must be identical.
    assert len(set(counts)) == 1, counts
    # Guard band below the pre-fix cost for this representative message
    # (pre-fix total was ~23 lookups; collapsing duplicated fields saves 3).
    assert counts[0] <= 21, counts[0]
