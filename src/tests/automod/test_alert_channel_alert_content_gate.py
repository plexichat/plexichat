"""Regression: N711 gated the moderator *notification* built by
``AlertModeratorsAction`` but left the action's second content-bearing
producer — the channel alert — completely ungated.

``_send_channel_alert`` reposts ``violation.matched_content`` (the private
offending message body) verbatim into the server's configured alert channel.
The alert channel is a *different* audience from the private channel the
violation happened in: any member who can view the (often public) alert
channel but is denied view on the violating channel receives the private body.
Unlike ``_send_notifications``, this path has no per-recipient view/read gate
at all.

The fix strips the raw body from a cross-channel alert and only includes it
when the alert is written back into the very channel the message came from,
whose viewers already have access to the original. Fail closed: an
unresolvable/missing channel id redacts too.
"""

from types import SimpleNamespace

import pytest

from src.core.automod.actions.alert import AlertModeratorsAction
from src.core.automod.models import ActionType, RuleType, Violation, ViolationSeverity

SERVER = 771
PRIVATE_CHANNEL = 882
ALERT_CHANNEL = 994
BOT = 5000
PRIVATE_BODY = "PRIVATE-CHANNEL-ALERT-BODY-MARKER"


class _FakeDB:
    def fetch_one(self, *args, **kwargs):
        return {"conversation_id": 999}


class _FakeMessaging:
    def __init__(self):
        self.sent = []

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=len(self.sent))


def _violation():
    return Violation(
        id=1,
        server_id=SERVER,
        channel_id=PRIVATE_CHANNEL,
        user_id=42,
        message_id=500,
        rule_id=1,
        rule_type=RuleType.KEYWORD,
        matched_content=PRIVATE_BODY,
        actions_taken=[ActionType.ALERT_MODERATORS],
        severity=ViolationSeverity.MEDIUM,
        created_at=0,
    )


def _run(alert_channel_id):
    messaging = _FakeMessaging()
    action = AlertModeratorsAction(db=_FakeDB(), messaging_module=messaging)
    action._send_channel_alert(alert_channel_id, _violation(), {"bot_user_id": BOT})
    assert messaging.sent, "control: the alert message must actually be sent"
    return messaging.sent[0]["content"]


@pytest.mark.automod
def test_cross_channel_alert_does_not_repost_private_body():
    """The decisive leak: a public alert channel carries a private body today."""
    content = _run(ALERT_CHANNEL)
    assert PRIVATE_BODY not in content, (
        "the channel alert reposted the private violating message body to an "
        "audience that can view the alert channel but not the violating channel"
    )
    # Metadata-only context must survive the redaction.
    assert "AutoMod" in content
    assert "<@42>" in content


@pytest.mark.automod
def test_same_channel_alert_keeps_the_body():
    """Control: viewers of the violating channel already have the original."""
    assert PRIVATE_BODY in _run(PRIVATE_CHANNEL)


@pytest.mark.automod
def test_unresolvable_alert_channel_strips_the_body():
    """Fail closed: a missing/unparseable channel id must redact the body."""
    for channel_id in (None, 0, "not-an-id"):
        assert PRIVATE_BODY not in _run(channel_id)
