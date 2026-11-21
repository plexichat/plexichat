"""AutoMod moderator-alert recipients must be genuine ints from the roster rows.

``AlertModeratorsAction._send_notifications`` builds the broadcast audience::

    audience = list(dict.fromkeys(int(mid) for mid in moderator_ids))

``moderator_ids`` comes straight from ``_get_moderator_ids``: the raw
``srv_servers.owner_id`` and the ``srv_members.user_id`` projection of the
moderator-role join, with no type filtering.  The ``int()`` coercion launders a
malformed-but-coercible value (``"07"``->7, ``" 8 "``->8, ``7.9``->7,
``True``->1) into a real user id, adding the *wrong* principal to the
ALERT_MODERATORS notification audience.  The downstream
``_should_notify_user_optimized`` gate only consults notification preferences
and focus -- it never re-verifies that the recipient is a moderator -- so the
coercion is the sole authority gate.  Only an exact integer roster id may
attest a moderator recipient; ``type()`` also rejects bool.
"""

from types import SimpleNamespace

import pytest

from src.core.automod.actions.alert import AlertModeratorsAction
from src.core.automod.models import ActionType, RuleType, Violation, ViolationSeverity

pytestmark = pytest.mark.automod

SERVER = 321
CHANNEL = 654
_GENUINE_MOD = 9001


class _FakeDB:
    def fetch_one(self, *args, **kwargs):
        return {"conversation_id": 999}


class _Notifications:
    def __init__(self, manager):
        self._manager = manager

    def _get_manager(self):
        return self._manager


class _SpyManager:
    def __init__(self):
        self.bulk_settings_calls = []
        self.created = []
        self.unread = []

    def get_notification_settings_bulk(self, user_ids, server_id=None):
        self.bulk_settings_calls.append(tuple(user_ids))
        return {uid: SimpleNamespace(user_id=uid) for uid in user_ids}

    def get_channel_overrides_bulk(self, user_ids, channel_id):
        return {}

    def _should_notify_user_optimized(self, *args, **kwargs):
        return True

    def _create_notification(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=1)

    def _update_unread_count(self, *args, **kwargs):
        self.unread.append((args, kwargs))


def _violation():
    return Violation(
        id=1,
        server_id=SERVER,
        channel_id=CHANNEL,
        user_id=42,
        message_id=None,
        rule_id=1,
        rule_type=RuleType.KEYWORD,
        matched_content="bad",
        actions_taken=[ActionType.ALERT_MODERATORS],
        severity=ViolationSeverity.MEDIUM,
        created_at=0,
    )


def _run(moderator_ids):
    spy = _SpyManager()
    action = AlertModeratorsAction(db=_FakeDB())
    action._notifications = _Notifications(spy)
    action._send_notifications(moderator_ids, _violation(), None)
    return spy


# Pre-fix: int(malformed) -> coerced_id -> that user joins the mod-alert audience.
_MALFORMED = [
    ("07", 7),  # leading-zero string -> 7
    (" 8 ", 8),  # padded string -> 8
    (7.9, 7),  # float truncates to 7
    ("5", 5),  # plain numeric string -> 5
    (True, 1),  # bool -> 1
]


@pytest.mark.parametrize("bad_value, coerced_id", _MALFORMED)
def test_malformed_moderator_id_excluded_from_alert_audience(bad_value, coerced_id):
    spy = _run([_GENUINE_MOD, bad_value])

    notified = [c["user_id"] for c in spy.created]
    assert coerced_id not in notified, (
        f"malformed moderator id {bad_value!r} was int()-coerced into "
        f"alerting wrong user {coerced_id}"
    )
    # Non-vacuous: the genuine moderator must still be notified.
    assert _GENUINE_MOD in notified

    # The bulk config lookup must not even include the coerced recipient.
    assert spy.bulk_settings_calls, "bulk settings audience was never resolved"
    audience = spy.bulk_settings_calls[0]
    assert coerced_id not in audience
    assert _GENUINE_MOD in audience


def test_genuine_integer_moderator_still_alerted():
    spy = _run([_GENUINE_MOD])
    assert [c["user_id"] for c in spy.created] == [_GENUINE_MOD]
