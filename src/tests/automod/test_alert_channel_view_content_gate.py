"""Regression: an ALERT_MODERATORS notification must not carry private
message content to a moderator who cannot view the violating channel.

``NotificationCreatorMixin.create_notifications`` (message mentions) gates its
fan-out on ``channels.view`` for every recipient.  The automod alert action is
a *second* notification producer: it formats the offending message body into
``content_preview`` and calls the creator's low-level ``_create_notification``
directly, bypassing that fan-out gate entirely.  A moderator whose channel
override denies ``messages.read``/``channels.view`` on the private channel
therefore still received the private body in both the websocket
``NOTIFICATION_CREATE`` event and the device push payload.

The fix installs an authoritative per-recipient channel-view gate *before* the
preview is attached and strips the content for denied moderators (fail closed:
an unwired/raising permission store denies too).
"""

from types import SimpleNamespace

import pytest

from src.core.automod.actions.alert import AlertModeratorsAction
from src.core.automod.models import ActionType, RuleType, Violation, ViolationSeverity

SERVER = 771
CHANNEL = 882
PRIVATE_BODY = "PRIVATE-VIOLATION-BODY-MARKER"
ALLOWED = 9101
DENIED = 9102


class _FakeDB:
    def fetch_one(self, *args, **kwargs):
        return {"id": 500, "conversation_id": 999, "channel_id": CHANNEL}


class _FakeNotifications:
    def __init__(self, manager):
        self._manager = manager

    def _get_manager(self):
        return self._manager


class _FakeServers:
    def __init__(self, allowed_ids):
        self.allowed_ids = set(allowed_ids)

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        return int(user_id) in self.allowed_ids


class _SpyManager:
    """Captures created alerts; models the creator's real collaborators."""

    def __init__(self, *, servers=None, permission_raises=False):
        self._servers = servers
        self.permission_raises = permission_raises
        self.created = []

    def get_notification_settings_bulk(self, user_ids, server_id=None):
        return {uid: SimpleNamespace(user_id=uid) for uid in user_ids}

    def get_channel_overrides_bulk(self, user_ids, channel_id):
        return {}

    def _should_notify_user_optimized(self, *args, **kwargs):
        return True

    def _create_notification(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=len(self.created))

    def _update_unread_count(self, *args, **kwargs):
        return None


def _violation():
    return Violation(
        id=1,
        server_id=SERVER,
        channel_id=CHANNEL,
        user_id=42,
        message_id=500,
        rule_id=1,
        rule_type=RuleType.KEYWORD,
        matched_content=PRIVATE_BODY,
        actions_taken=[ActionType.ALERT_MODERATORS],
        severity=ViolationSeverity.MEDIUM,
        created_at=0,
    )


def _run(manager, audience=(ALLOWED, DENIED)):
    action = AlertModeratorsAction(db=_FakeDB())
    action._notifications = _FakeNotifications(manager)
    action._send_notifications(list(audience), _violation(), None)
    return {c["user_id"]: c["content_preview"] for c in manager.created}


@pytest.mark.automod
def test_denied_view_moderator_gets_no_private_content():
    """The decisive leak: a moderator denied view still gets the body today."""
    manager = _SpyManager(servers=_FakeServers({ALLOWED}))
    previews = _run(manager)

    assert PRIVATE_BODY in previews[ALLOWED], "control: viewer keeps the preview"
    assert PRIVATE_BODY not in previews[DENIED], (
        "a moderator who cannot view the channel received the private "
        "violation body in an alert notification"
    )


@pytest.mark.automod
def test_unwired_or_raising_permission_store_strips_content():
    """Fail closed: no authoritative view check means no private content."""
    for manager in (
        _SpyManager(servers=None),
        _SpyManager(servers=_RaisingServers()),
    ):
        previews = _run(manager, audience=(DENIED,))
        assert PRIVATE_BODY not in previews[DENIED]


class _RaisingServers:
    def has_permission(self, user_id, server_id, permission, channel_id=None):
        raise RuntimeError("permission store unavailable")
