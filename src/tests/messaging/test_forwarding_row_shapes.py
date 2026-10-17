"""Tuple-row adapters for the forwarding read path.

Admin database adapters may return mapping or tuple rows (see
docs/admin-hardening.md). These tests pin the tuple shape for
``forward_message`` (original-message and COUNT(*) reads),
``get_forward_history``, and ``get_forwards_by_user`` in
``src/core/messaging/services/forwarding.py``.

Run with: pytest src/tests/messaging/test_forwarding_row_shapes.py -q
"""

import pytest


# A ``msg_forwarded`` row in schema order (see ``src/core/messaging/schema.py``).
_FORWARDED_ROW = (
    1,  # id
    10,  # message_id
    20,  # original_message_id
    2,  # original_conversation_id
    5,  # original_author_id
    7,  # forwarded_by
    None,  # original_content
    100,  # original_created_at
    200,  # created_at
)

# ``SELECT id, conversation_id, author_id, content, content_encrypted,
# created_at`` column order in ``forward_message``.
_MESSAGE_ROW = (20, 2, 5, "hello", None, 100)


class _TupleForwardingDB:
    """Bare-tuple rows, as a raw cursor returns them."""

    def __init__(self, one=None, all_rows=None, one_sequence=()):
        self._one = one
        self._all_rows = list(all_rows or [])
        self._one_sequence = list(one_sequence)
        self.executed = []

    def fetch_one(self, query, params=()):
        if self._one_sequence:
            return self._one_sequence.pop(0)
        if "COUNT(*)" in query:
            return (0,)
        return self._one

    def fetch_all(self, query, params=()):
        return list(self._all_rows)

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Participants:
    def is_participant(self, conversation_id, user_id):
        return True


def _make_service(db):
    from src.core.messaging.services.forwarding import ForwardingService

    return ForwardingService(db, participant_svc=_Participants())


@pytest.mark.messaging
class TestForwardingTupleRows:
    def test_forward_history_maps_tuple_rows(self):
        service = _make_service(_TupleForwardingDB(one=(2,), all_rows=[_FORWARDED_ROW]))

        rows = service.get_forward_history(20, 1)

        assert len(rows) == 1
        assert rows[0]["id"] == 1
        assert rows[0]["original_message_id"] == 20
        assert rows[0]["forwarded_by"] == 7

    def test_forward_history_maps_mapping_rows(self):
        service = _make_service(
            _TupleForwardingDB(
                one={"conversation_id": 2},
                all_rows=[
                    dict(
                        zip(
                            (
                                "id",
                                "message_id",
                                "original_message_id",
                                "original_conversation_id",
                                "original_author_id",
                                "forwarded_by",
                                "original_content",
                                "original_created_at",
                                "created_at",
                            ),
                            _FORWARDED_ROW,
                        )
                    )
                ],
            )
        )

        rows = service.get_forward_history(20, 1)

        assert len(rows) == 1
        assert rows[0]["id"] == 1

    def test_forwards_by_user_maps_tuple_rows(self):
        service = _make_service(_TupleForwardingDB(all_rows=[_FORWARDED_ROW]))

        rows = service.get_forwards_by_user(7)

        assert len(rows) == 1
        assert rows[0]["message_id"] == 10
        assert rows[0]["forwarded_by"] == 7

    def test_forwards_by_user_clamps_negative_limit(self):
        seen = []

        class _RecordingDB(_TupleForwardingDB):
            def fetch_all(self, query, params=()):
                seen.append(tuple(params))
                return []

        service = _make_service(_RecordingDB())

        assert service.get_forwards_by_user(7, limit=-1) == []
        assert seen[0][1] == 0

    def test_forward_message_reads_tuple_rows(self):
        service = _make_service(
            _TupleForwardingDB(
                one_sequence=[_MESSAGE_ROW, (0,), (0,)],
                all_rows=[],
            )
        )
        service._generate_id = lambda: 99
        service._safe_send_via_messaging = lambda *a: {"id": 100}

        result = service.forward_message(5, 20, 2)

        assert result["original_message_id"] == 20
        assert result["original_author_id"] == 5
        assert result["original_conversation_id"] == 2
        assert result["new_message_id"] == 100

    def test_forward_message_enforces_rate_limit_from_tuple_count(self):
        service = _make_service(_TupleForwardingDB(one_sequence=[_MESSAGE_ROW, (50,)]))

        with pytest.raises(ValueError, match="Forward rate limit exceeded"):
            service.forward_message(5, 20, 2)
