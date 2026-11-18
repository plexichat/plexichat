"""Regression tests: bulk notification-setting lookups must chunk IN clauses.

``SettingsMixin.get_notification_settings_bulk`` and
``get_channel_overrides_bulk`` built a single ``WHERE user_id IN (?,?,...)``
with one bind variable per requested id. The number of ids is not bounded by
the manager: a single ``@everyone``/role mention in a large server (or a
message carrying many user mentions) fans out to every recipient and hands the
whole list to these helpers. On SQLite that crosses the 999/32766 bind-variable
cap and raises ``OperationalError: too many SQL variables``, so building a
notification for a legitimate large-server mention crashes the send path — a
denial of service driven entirely by message content.

The fix chunks the lookups (the 500-per-chunk pattern the search indexer
already uses) so any number of ids is handled without exceeding backend
limits. These tests spy on the emitted SQL rather than relying on a particular
SQLite build's variable ceiling.
"""

from src.core.notifications.models import NotificationLevel


CHUNK_SIZE = 500
MANY_IDS = list(range(1, 3 * CHUNK_SIZE + 7))  # spans four chunks


class _RecordingDB:
    """Delegate to the real DB while recording emitted SQL and params."""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, tuple(params)))
        return self._real.fetch_all(sql, params)


def _spy_on(manager):
    spy = _RecordingDB(manager._db)
    manager._db = spy
    return manager, spy


def _restore(manager, real_db):
    manager._db = real_db


def test_bulk_notification_settings_chunks_in_clause(notification_manager):
    """Thousands of ids must not produce a single oversized IN clause."""
    real_db = notification_manager._db
    server_id = 4242
    manager, spy = _spy_on(notification_manager)
    try:
        result = manager.get_notification_settings_bulk(MANY_IDS, server_id)
    finally:
        _restore(manager, real_db)

    # The lookup was split into several small queries, never one giant one.
    assert len(spy.calls) > 1
    for sql, params in spy.calls:
        # CHUNK_SIZE id binds plus the single server_id bind.
        assert sql.count("?") <= CHUNK_SIZE + 1, sql
        assert len(params) <= CHUNK_SIZE + 1
        # The server_id filter is present in every chunk and bound once there.
        assert "server_id = ?" in sql
        assert params[-1] == server_id

    # Every requested id is returned with the default per-user settings.
    assert set(result) == set(MANY_IDS)
    for uid in MANY_IDS:
        settings = result[uid]
        assert settings.user_id == uid
        assert settings.server_id == server_id
        assert settings.level == NotificationLevel.ALL_MESSAGES


def test_bulk_notification_settings_merges_rows_from_multiple_chunks(
    notification_manager,
):
    """Persisted settings in different chunks are merged, not overwritten."""
    server_id = 555
    # Two ids that land in different chunks.
    first, second = 5, CHUNK_SIZE + 9
    notification_manager.update_notification_settings(
        first, server_id, level=NotificationLevel.ONLY_MENTIONS
    )
    notification_manager.update_notification_settings(
        second, server_id, level=NotificationLevel.NOTHING
    )

    result = notification_manager.get_notification_settings_bulk(MANY_IDS, server_id)

    assert result[first].level == NotificationLevel.ONLY_MENTIONS
    assert result[second].level == NotificationLevel.NOTHING


def test_bulk_channel_overrides_chunks_in_clause(notification_manager):
    """The channel-override bulk lookup has the same unbounded IN clause."""
    real_db = notification_manager._db
    channel_id = 9999
    manager, spy = _spy_on(notification_manager)
    try:
        result = manager.get_channel_overrides_bulk(MANY_IDS, channel_id)
    finally:
        _restore(manager, real_db)

    assert len(spy.calls) > 1
    for sql, params in spy.calls:
        # CHUNK_SIZE id binds plus the single channel_id bind.
        assert sql.count("?") <= CHUNK_SIZE + 1, sql
        assert len(params) <= CHUNK_SIZE + 1
        assert params[0] == channel_id

    # No override rows exist for these ids, so the result is empty.
    assert result == {}
