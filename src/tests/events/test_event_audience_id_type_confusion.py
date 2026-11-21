"""Core event audiences must not launder malformed ids into recipients.

Two boundaries used to coerce raw ids with ``int()``:

* ``resolve_channel_event_recipients._final`` is the common final dedupe/cache
  step of the channel audience resolver. Its inputs include the raw
  ``srv_members.user_id`` projection (``get_member_user_ids``) and the raw
  ``msg_participants.user_id`` projection (``get_participant_ids``). Coercion
  there admits a principal that never proved view/participation rights.
* ``EventManager._publish_cross_worker`` stamps the audience into the envelope
  with ``[int(uid) for uid in recipients]``. The router dedupes/caps but does
  not type-check, so a coercible value reaching this boundary is laundered
  into the wire audience of sibling workers.

Only genuine ``int`` values may enter either audience; bool and coercible
strings/floats must be dropped.
"""

import json
from unittest.mock import MagicMock

import pytest

import src.api as api
import src.core.database as database_module
from src.core.events.models import Event
from src.core.events.types import EventType

pytestmark = pytest.mark.unit

_CHANNEL = 333
_GUILD = 222
# Raw projection-style audience: genuine 7 and 42 plus coercible junk that
# int() launders into 5, 8 and 1.
_RAW = ["05", 7, " 8 ", True, 5.9, 42]


class _Channel:
    server_id = _GUILD


class _Servers:
    def get_channel(self, channel_id, user_id):
        return _Channel()

    def has_permission(self, user_id, server_id, perm, channel_id):
        # Real has_permission coerces int(user_id), so coercible junk passes.
        return True

    def get_member_user_ids(self, server_id):
        return list(_RAW)


@pytest.fixture(autouse=True)
def _threads_unavailable(monkeypatch):
    from src.core import threads

    monkeypatch.setattr(threads, "is_setup", lambda: False)


def test_resolver_rejects_coerced_member_ids(monkeypatch):
    monkeypatch.setattr(api, "get_servers", lambda: _Servers())
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_relationships", lambda: None)
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    got = resolve_channel_event_recipients(_CHANNEL, guild_id=_GUILD)

    assert got == [7, 42]
    for wrong in (5, 8, 1):
        assert wrong not in got, f"laundered viewer id {wrong} admitted"


def test_envelope_rejects_coerced_recipient_ids(events_module, monkeypatch):
    em = events_module._manager
    client = MagicMock()
    monkeypatch.setattr(database_module, "valkey_available", lambda: True)
    monkeypatch.setattr(database_module, "get_valkey_client", lambda: client)

    em._publish_cross_worker(
        Event(event_type=EventType.PRESENCE_UPDATE, data={}), list(_RAW)
    )

    _, payload = client.publish.call_args[0]
    body = json.loads(payload)
    assert body["u"] == [7, 42]


def test_envelope_preserves_all_genuine_ints(events_module, monkeypatch):
    # Non-vacuous: a clean all-int audience must be stamped intact.
    em = events_module._manager
    client = MagicMock()
    monkeypatch.setattr(database_module, "valkey_available", lambda: True)
    monkeypatch.setattr(database_module, "get_valkey_client", lambda: client)

    em._publish_cross_worker(
        Event(event_type=EventType.PRESENCE_UPDATE, data={}), [10, 20, 30, 20]
    )

    _, payload = client.publish.call_args[0]
    assert json.loads(payload)["u"] == [10, 20, 30, 20]
