"""INTERACTION_CREATE must fail closed when the bot-owner lookup is unresolved.

``InteractionHandler.dispatch_interaction`` builds an INTERACTION_CREATE event
carrying the interaction's private body: the interaction ``token`` and the
``data`` block with user-supplied ``option`` values (and nested ``resolved``
users/members/channels/roles). The only legitimate recipient is the owning bot,
resolved via ``SELECT bot_id FROM app_applications WHERE id = ?``.

When that lookup fails (no row / no ``bot_id``), the emitter fell through to
``self._events.dispatch(event)`` -- i.e. ``user_ids=None``. The EventRouter
treats ``None`` as "route by scope", so a server-scoped event is widened to
*every* member of the server, leaking the private token and interaction body to
a non-participant. The audience must instead resolve to an empty list, never
``None``. This mirrors the artifact fail-closed fix (5525788b) and the DM-mention
notification fix (b62dad3b).
"""

from typing import Any, Dict, List, Optional

from src.core.applications.interactions.handler import InteractionHandler
from src.core.applications.models import (
    Interaction,
    InteractionData,
    InteractionType,
)
from src.core.events.router import EventRouter

PRIVATE_TOKEN = "private-interaction-token-do-not-broadcast"
PRIVATE_OPTION = "user-secret-option-value"
NON_PARTICIPANT = 7777777
BOT_OWNER = 1001
SERVER_ID = 940001
CHANNEL_ID = 930001


class _CapturingEvents:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def dispatch(self, event, user_ids=None, server_id=None, channel_id=None, **kwargs):
        self.calls.append(
            {
                "event": event,
                "user_ids": user_ids,
                "server_id": server_id,
                "channel_id": channel_id,
            }
        )
        return 1


class _FakeDB:
    def __init__(self, row: Optional[Dict[str, Any]]) -> None:
        self._row = row

    def fetch_one(self, query, params=None):
        return self._row


class _Servers:
    """A server whose only member is a non-participant in the interaction."""

    def get_member_user_ids(self, server_id):
        return [NON_PARTICIPANT]


def _interaction() -> Interaction:
    data = InteractionData(
        id=910011,
        name="config",
        options=[{"name": "secret", "type": 3, "value": PRIVATE_OPTION}],
    )
    return Interaction(
        id=910001,
        application_id=920001,
        interaction_type=InteractionType.APPLICATION_COMMAND,
        data=data,
        server_id=SERVER_ID,
        channel_id=CHANNEL_ID,
        user_id=1002,
        token=PRIVATE_TOKEN,
        version=1,
        message_id=None,
        locale=None,
        server_locale=None,
        created_at=1,
    )


def _dispatch(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    events = _CapturingEvents()
    handler = InteractionHandler(_FakeDB(row), {}, events_module=events)
    handler.dispatch_interaction(_interaction())
    assert len(events.calls) == 1, "expected one INTERACTION_CREATE dispatch"
    return events.calls[0]


def _router_recipients(event, user_ids):
    """Route the captured audience through the real, widening EventRouter."""
    router = EventRouter(servers_module=_Servers())
    return router.get_recipients(
        event,
        user_ids=user_ids,
        server_id=SERVER_ID,
        channel_id=CHANNEL_ID,
    )


def test_bot_lookup_no_row_fails_closed():
    call = _dispatch(None)
    assert call["user_ids"] == [], (
        "a failed bot-owner lookup must yield an empty audience; None lets "
        "the router widen the private interaction token/body to every "
        "server member"
    )


def test_bot_lookup_missing_bot_id_fails_closed():
    call = _dispatch({"bot_id": None})
    assert call["user_ids"] == []


def test_bot_lookup_success_still_targets_bot():
    """Positive control: a resolved bot owner is still the sole recipient."""
    call = _dispatch({"bot_id": BOT_OWNER})
    assert call["user_ids"] == [BOT_OWNER]


def test_failure_would_leak_if_audience_were_none():
    """Non-vacuity: prove the private event is server-scoped and carries a
    secret, and that `None` really widens it to a non-participant while `[]`
    does not. This is the exact consequence the fail-closed fix prevents.
    """
    call = _dispatch(None)
    event = call["event"]
    payload = event.data

    # The payload really is private content, and the event really is
    # server-scoped (the widening precondition).
    assert payload["token"] == PRIVATE_TOKEN
    assert payload["data"]["options"][0]["value"] == PRIVATE_OPTION
    assert event.server_id == SERVER_ID

    # Counterfactual: the buggy `None` audience reaches the non-participant.
    leaked = _router_recipients(event, None)
    assert NON_PARTICIPANT in leaked, (
        "test must demonstrate that user_ids=None widens to the "
        "non-participant server member (otherwise the test is vacuous)"
    )

    # The fixed `[]` audience reaches nobody.
    assert _router_recipients(event, []) == []
