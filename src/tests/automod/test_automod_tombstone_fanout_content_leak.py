"""Regression: the AutoMod tombstone fan-out must not carry the private body.

N948: ``DeleteMessageAction._replace_message`` stored
``violation.matched_content`` (the offending message text) in the tombstone's
``metadata.automod_reason``. ``_broadcast_updated`` then serialized that
message once and fanned the resulting MESSAGE_UPDATE out to every
``channels.view`` recipient. A moderator who can view the channel but is
denied ``messages.read`` therefore received the private body on the wire.
``_fanout_safe_message_body`` stripped poll/read/reaction viewer fields but
not this metadata key.

Two layers fix it, and both are exercised here:

  * the stored reason is a neutral rule/action label (never content), and
  * the fan-out sanitizer drops ``metadata.automod_reason`` from the wire copy
    even for already-stored legacy rows / direct ``automod_remove_message``
    callers.

The non-vacuity assertions prove the secret is genuinely present in the raw
serializer output and that the pre-fix reason expression really used the
private body, so a no-op sanitizer cannot make these tests pass. Legitimate
audit pointers (``automod_removed``, ``automod_violation_id``, ``automod_rule``)
and the audit-log row's own reason are left intact.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import src.api as api
import src.api.websocket as ws_module
from src.api.routes.messages.helpers import _message_to_response
from src.api.routes.messages.messages_crud.broadcast import _fanout_safe_message_body
from src.core import automod
from src.core.automod.actions.delete import DeleteMessageAction
from src.core.automod.models import (
    ActionType,
    RuleAction,
    RuleType,
    Violation,
    ViolationSeverity,
)

SERVER = 771
CHANNEL = 882
MSG_ID = 500
RULE_ID = 42
PRIVATE_BODY = "PRIVATE-VIOLATION-BODY-MARKER-N948"
# This moderator holds channels.view on the channel but is denied messages.read.
VIEW_ONLY_MODERATOR = 9102


class _RecordingDispatcher:
    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, list(user_ids)))


class _RecordingMessaging:
    """Captures the tombstone write and returns the serialized message."""

    def __init__(self, metadata):
        self._metadata = metadata
        self.remove_calls = []

    def automod_remove_message(self, message_id, **kwargs):
        self.remove_calls.append({"message_id": message_id, **kwargs})
        return SimpleNamespace(
            id=message_id,
            author_id=77,
            content="",
            created_at=1_700_000_000_000,
            channel_id=CHANNEL,
            conversation_id=CHANNEL,
            metadata=dict(self._metadata),
            embeds=[],
            attachments=[],
        )


def _violation(**overrides):
    fields = dict(
        id=1,
        server_id=SERVER,
        channel_id=CHANNEL,
        user_id=42,
        message_id=MSG_ID,
        rule_id=RULE_ID,
        rule_type=RuleType.KEYWORD,
        matched_content=PRIVATE_BODY,
        actions_taken=[ActionType.DELETE_MESSAGE],
        severity=ViolationSeverity.MEDIUM,
        created_at=0,
    )
    fields.update(overrides)
    return Violation(**fields)


def _tombstone_msg(**metadata):
    return SimpleNamespace(
        id=MSG_ID,
        author_id=77,
        content="",
        created_at=1_700_000_000_000,
        channel_id=CHANNEL,
        conversation_id=CHANNEL,
        metadata=dict(metadata),
        embeds=[],
        attachments=[],
    )


@pytest.mark.automod
def test_replacement_reason_is_neutral_not_private_content(monkeypatch):
    """The stored tombstone reason must never contain the offending body."""
    # A peer automod test leaves a lightweight ``src.api`` stub in sys.modules,
    # so the module object bound at import time may lack these accessors.
    monkeypatch.setattr(ws_module, "is_setup", lambda: False, raising=False)
    monkeypatch.setattr(
        automod,
        "get_rule",
        lambda rule_id: SimpleNamespace(name="Blocked words"),
        raising=False,
    )
    messaging = _RecordingMessaging({})
    action = DeleteMessageAction(None, None, messaging, None)

    ok = action.execute(
        RuleAction(action_type=ActionType.DELETE_MESSAGE),
        _violation(),
        context={
            "__automod_replacement__": True,
            # Even a caller-supplied "reason" cannot smuggle content back in.
            "__automod_reason__": PRIVATE_BODY,
        },
    )

    assert ok is True
    assert len(messaging.remove_calls) == 1
    reason = messaging.remove_calls[0]["reason"]
    # Non-vacuity: the violation really carried the private body ...
    assert _violation().matched_content == PRIVATE_BODY
    # ... yet the value handed to the tombstone writer is a content-free label.
    assert reason is not None
    assert PRIVATE_BODY not in reason
    assert str(RULE_ID) in reason
    assert ActionType.DELETE_MESSAGE.value in reason


@pytest.mark.automod
def test_fanout_body_strips_automod_reason_but_keeps_audit_pointers(monkeypatch):
    """A serialized tombstone body loses only the private reason."""
    monkeypatch.setattr(api, "get_embeds", lambda: None, raising=False)
    msg = _tombstone_msg(
        automod_removed=True,
        automod_violation_id=987,
        automod_rule="Blocked words",
        automod_reason=PRIVATE_BODY,
    )
    raw = _message_to_response(msg).model_dump()

    # Non-vacuity: the unsanitized serializer really carries the private body.
    assert raw["metadata"]["automod_reason"] == PRIVATE_BODY

    safe = _fanout_safe_message_body(raw)
    assert PRIVATE_BODY not in json.dumps(safe)
    assert "automod_reason" not in safe["metadata"]
    # Legitimate tombstone/audit pointers survive for clients + admin deep link.
    assert safe["metadata"]["automod_removed"] is True
    assert safe["metadata"]["automod_violation_id"] == 987
    assert safe["metadata"]["automod_rule"] == "Blocked words"
    # The pre-existing viewer-field stripping is not regressed.
    assert safe["poll_results"] is None
    assert safe["read"] is False


@pytest.mark.automod
def test_fanout_drops_serialized_metadata_blob_fail_closed():
    """A serialized metadata blob cannot be field-stripped -- drop it."""
    blob = json.dumps({"automod_reason": PRIVATE_BODY, "automod_removed": True})
    safe = _fanout_safe_message_body({"metadata": blob})
    assert PRIVATE_BODY not in json.dumps(safe)
    assert safe["metadata"] is None


@pytest.mark.automod
def test_view_only_moderator_message_update_has_no_private_body(monkeypatch):
    """The decisive leak: the view-only recipient's wire body is clean."""
    dispatcher = _RecordingDispatcher()
    monkeypatch.setattr(ws_module, "is_setup", lambda: True, raising=False)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: dispatcher, raising=False)
    monkeypatch.setattr(api, "get_auth", lambda: None, raising=False)
    monkeypatch.setattr(api, "get_reactions", lambda: None, raising=False)
    monkeypatch.setattr(api, "get_embeds", lambda: None, raising=False)

    # Models an already-stored legacy row (or a direct automod_remove_message
    # caller) whose metadata still carries the private reason. The sanitizer
    # must be the last line of defence even after the writer-side fix.
    msg = _tombstone_msg(
        automod_removed=True,
        automod_violation_id=987,
        automod_reason=PRIVATE_BODY,
    )

    action = DeleteMessageAction(None, None, None, None)
    # Audience resolution already enforces channels.view in production; this
    # test pins the audience to a view-only moderator denied messages.read so
    # it isolates the body contents delivered to that viewer.
    monkeypatch.setattr(
        action,
        "_resolve_recipients",
        lambda violation, context: ([VIEW_ONLY_MODERATOR], SERVER),
    )

    action._broadcast_updated(_violation(), msg, None)

    assert len(dispatcher.dispatched) == 1
    event, recipients = dispatcher.dispatched[0]
    assert recipients == [VIEW_ONLY_MODERATOR]
    assert event.data.get("id") == str(MSG_ID)
    assert PRIVATE_BODY not in json.dumps(event.data)

    # Non-vacuity: the same message's raw serializer body DOES contain the
    # secret, so it is the sanitizer in _broadcast_updated that removes it.
    assert PRIVATE_BODY in json.dumps(_message_to_response(msg).model_dump())
