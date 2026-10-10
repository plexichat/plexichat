"""Regression tests for transcript-export date bounds.

These pin the ``TranscriptExportRequest.timezone`` contract: a date without an
explicit UTC offset must be interpreted in the requested zone (default UTC)
rather than the server's local time, and an explicit offset in the value must
always win. The previous implementation resolved naive values with the host's
local time zone, so the same request filtered differently on different hosts.
"""

from src.core.messaging.services.export import TranscriptExportService


class _StubMessages:
    def __init__(self, msgs):
        self._msgs = msgs

    def get_by_conversation(
        self, conversation_id, limit=10000, from_ts=None, to_ts=None, ascending=False
    ):
        return list(self._msgs[:limit])


class _StubParticipants:
    def exists(self, conversation_id, user_id):
        return True


# 2024-01-01T00:00:00Z
_MIDNIGHT_UTC_MS = 1704067200000


def _svc_at(created_at_ms):
    msgs = [
        {
            "id": "1",
            "author_id": "42",
            "content": "hi",
            "created_at": created_at_ms,
        }
    ]
    return TranscriptExportService(None, _StubMessages(msgs), _StubParticipants())


def test_naive_date_defaults_to_utc():
    svc = _svc_at(_MIDNIGHT_UTC_MS)
    # Equal bound is inclusive.
    assert svc._get_messages_for_export(42, 7, from_date="2024-01-01T00:00:00")
    # One second later excludes the message.
    assert svc._get_messages_for_export(42, 7, from_date="2024-01-01T00:00:01") == []
    # to_date is inclusive of its instant.
    assert svc._get_messages_for_export(42, 7, to_date="2024-01-01T00:00:00")
    assert svc._get_messages_for_export(42, 7, to_date="2023-12-31T23:59:59") == []


def test_timezone_argument_is_honoured():
    svc = _svc_at(_MIDNIGHT_UTC_MS)
    # 20:00 in New York (UTC-5) == 2024-01-01T01:00Z, after the 00:00Z message.
    assert (
        svc._get_messages_for_export(
            42, 7, from_date="2023-12-31T20:00:00", tz_name="America/New_York"
        )
        == []
    )
    # The same wall-clock string in UTC is before the message.
    assert svc._get_messages_for_export(
        42, 7, from_date="2023-12-31T20:00:00", tz_name="UTC"
    )


def test_explicit_offset_overrides_timezone():
    svc = _svc_at(_MIDNIGHT_UTC_MS)
    # An explicit offset always wins over the requested zone name.
    assert (
        svc._get_messages_for_export(
            42,
            7,
            from_date="2023-12-31T20:00:01-05:00",
            tz_name="America/New_York",
        )
        == []
    )


def test_unknown_timezone_falls_back_to_utc():
    svc = _svc_at(_MIDNIGHT_UTC_MS)
    assert svc._get_messages_for_export(
        42, 7, from_date="2023-12-31T20:00:00", tz_name="Not/AZone"
    )
