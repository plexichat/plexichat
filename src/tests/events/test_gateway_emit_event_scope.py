"""gateway_emit._emit must stamp routing scope onto the Event itself.

Intent filtering (local and cross-worker) reads ``Event.server_id`` /
``Event.channel_id``. Emitting a scope-less Event and passing scope only via
dispatch kwargs made every channel-scoped event classify as guild-scoped at
the intent gate, and made the cross-worker envelope carry ``[None, None]``.
"""

import pytest

import src.core.events as events
import src.core.events.gateway_emit as ge

pytestmark = pytest.mark.unit


def test_emit_stamps_authoritative_scope_on_event(monkeypatch):
    captured = {}

    def fake_dispatch(event, **kwargs):
        captured["event"] = event
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(events, "is_setup", lambda: True)
    monkeypatch.setattr(events, "dispatch", fake_dispatch)

    ge.emit_typing_start(channel_id=555, user_id=1, guild_id=7)

    event = captured["event"]
    assert event.server_id == 7
    assert event.channel_id == 555
    # Routing kwargs are unchanged.
    assert captured["kwargs"]["server_id"] == 7
    assert captured["kwargs"]["channel_id"] == 555
