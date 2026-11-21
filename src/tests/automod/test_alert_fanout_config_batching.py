"""Regression: ALERT_MODERATORS resolves moderator config in two bulk reads.

``_send_notifications`` used to call ``get_notification_settings`` and
``get_channel_override`` once per moderator. For a broadcast to N moderators
that is 2N config lookups (the settings helper does a cache get + a SELECT each
time). The fix resolves the whole audience through the existing bulk helpers,
which chunk their ``IN (...)`` binds and key results by user id.

Counters pinned below (N moderators):

* before -- N ``get_notification_settings`` + N ``get_channel_override`` calls;
* after  -- exactly one bulk settings read and one bulk override read, and zero
  per-user lookups, regardless of N.

Privacy / fail behaviour:

* each moderator gets its own settings/override row (no cross-user mixing);
* a bulk lookup failure notifies nobody (no partial fan-out, no leak).
"""

from types import SimpleNamespace

import pytest

from src.core.automod.actions.alert import AlertModeratorsAction
from src.core.automod.models import ActionType, RuleType, Violation, ViolationSeverity


SERVER = 321
CHANNEL = 654
AUDIENCE = list(range(9000, 9008))


class _FakeDB:
    def fetch_one(self, *args, **kwargs):
        return {"conversation_id": 999}


class _FakeNotifications:
    def __init__(self, manager):
        self._manager = manager

    def _get_manager(self):
        return self._manager


class _SpyManager:
    def __init__(self, *, fail_bulk=False, settings=None, overrides=None):
        self.fail_bulk = fail_bulk
        self.settings_map = settings or {}
        self.overrides_map = overrides or {}
        self.per_user_calls = []
        self.bulk_settings_calls = []
        self.bulk_override_calls = []
        self.created = []
        self.unread_updates = []
        self.gate_inputs = []

    # Per-user lookups are the regression: record any use of them.
    def get_notification_settings(self, user_id, server_id=None):
        self.per_user_calls.append(("settings", user_id))
        return self.settings_map.get(user_id)

    def get_channel_override(self, user_id, channel_id):
        self.per_user_calls.append(("override", user_id))
        return self.overrides_map.get(user_id)

    def get_notification_settings_bulk(self, user_ids, server_id=None):
        self.bulk_settings_calls.append(tuple(user_ids))
        if self.fail_bulk:
            raise RuntimeError("settings store unavailable")
        return {
            uid: self.settings_map.get(uid, SimpleNamespace(user_id=uid))
            for uid in user_ids
        }

    def get_channel_overrides_bulk(self, user_ids, channel_id):
        self.bulk_override_calls.append(tuple(user_ids))
        if self.fail_bulk:
            raise RuntimeError("override store unavailable")
        return {
            uid: self.overrides_map[uid]
            for uid in user_ids
            if uid in self.overrides_map
        }

    def _should_notify_user_optimized(
        self,
        user_id,
        author_id,
        server_id,
        channel_id,
        mention_type,
        settings,
        override,
    ):
        self.gate_inputs.append((user_id, settings, override))
        return True

    def _create_notification(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=1)

    def _update_unread_count(self, *args, **kwargs):
        self.unread_updates.append((args, kwargs))


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


def _run(manager, audience=None):
    action = AlertModeratorsAction(db=_FakeDB())
    action._notifications = _FakeNotifications(manager)
    action._send_notifications(
        list(AUDIENCE if audience is None else audience), _violation(), None
    )
    return action


@pytest.mark.automod
def test_alert_fanout_uses_two_bulk_config_reads():
    """Before: 2N per-user lookups. After: 1 bulk settings + 1 bulk override."""
    manager = _SpyManager()
    _run(manager)

    assert manager.per_user_calls == [], (
        f"fan-out still resolves config per moderator: {manager.per_user_calls}"
    )
    assert len(manager.bulk_settings_calls) == 1
    assert len(manager.bulk_override_calls) == 1
    assert set(manager.bulk_settings_calls[0]) == set(AUDIENCE)
    assert set(manager.bulk_override_calls[0]) == set(AUDIENCE)

    # Result parity: every moderator is still notified exactly once.
    assert [c["user_id"] for c in manager.created] == AUDIENCE
    assert len(manager.unread_updates) == len(AUDIENCE)


@pytest.mark.automod
def test_alert_fanout_keeps_each_moderators_config_scoped():
    """A moderator must only ever see its own settings/override row."""
    overrides = {
        AUDIENCE[0]: SimpleNamespace(user_id=AUDIENCE[0], level="MUTED"),
        AUDIENCE[3]: SimpleNamespace(user_id=AUDIENCE[3], level="MENTIONS"),
    }
    manager = _SpyManager(overrides=overrides)
    _run(manager)

    by_uid = {
        uid: (settings, override) for uid, settings, override in manager.gate_inputs
    }
    for uid in AUDIENCE:
        settings, override = by_uid[uid]
        assert settings.user_id == uid, "settings from another moderator leaked in"
        assert (override.user_id if override else None) in (None, uid), (
            "override from another moderator leaked in"
        )
    assert by_uid[AUDIENCE[0]][1] is overrides[AUDIENCE[0]]
    assert by_uid[AUDIENCE[2]][1] is None


@pytest.mark.automod
def test_alert_fanout_bulk_failure_notifies_nobody():
    """A failed bulk read fails closed: no partial fan-out, no crash leak."""
    manager = _SpyManager(fail_bulk=True)
    with pytest.raises(RuntimeError):
        _run(manager)  # execute()'s handler would turn this into a False return

    assert manager.created == [], "partial fan-out leaked notifications"
    assert manager.per_user_calls == [], "must not fall back to per-user reads"
