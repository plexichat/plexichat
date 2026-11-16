"""PlexiJoin record fields, identifiers and room rosters are bounded."""

import pytest

from src.core.plexijoin.manager import PlexiJoinManager
from src.core.plexijoin.validation import (
    MAX_INSTANCE_ID_LENGTH,
    MAX_MESSAGE_COUNT,
    MAX_NOTE_LENGTH,
    MAX_PAGE_SIZE,
    MAX_PARTICIPANTS_PER_ROOM,
    MAX_URL_LENGTH,
    bound_direction,
    bound_id,
    bound_message_count,
    bound_pagination,
    bound_text,
    validate_room_payload,
)
from src.core.security.hardening import HardeningViolation


class TestTextAndIds:
    @pytest.mark.parametrize(
        "bad", [None, "", 123, "x" * (MAX_INSTANCE_ID_LENGTH + 1), "a\nb"]
    )
    def test_required_text_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            bound_text(bad, "remote_instance_id", MAX_INSTANCE_ID_LENGTH, required=True)

    def test_optional_text(self):
        assert bound_text(None, "note", MAX_NOTE_LENGTH) is None
        assert bound_text("ok", "note", MAX_NOTE_LENGTH) == "ok"
        with pytest.raises(HardeningViolation):
            bound_text("x" * (MAX_NOTE_LENGTH + 1), "note", MAX_NOTE_LENGTH)

    @pytest.mark.parametrize("bad", [0, -1, 2**63, "abc", 1.5])
    def test_bad_ids_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            bound_id(bad, "connection_id")

    def test_pagination_bounded(self):
        assert bound_pagination(1, 50) == (1, 50)
        with pytest.raises(HardeningViolation):
            bound_pagination(0, 10)
        with pytest.raises(HardeningViolation):
            bound_pagination(1, MAX_PAGE_SIZE + 1)

    def test_direction_and_count(self):
        assert bound_direction("inbound") == "inbound"
        with pytest.raises(HardeningViolation):
            bound_direction("sideways")
        assert bound_message_count(3) == 3
        with pytest.raises(HardeningViolation):
            bound_message_count(MAX_MESSAGE_COUNT + 1)

    def test_url_length_bound(self):
        with pytest.raises(HardeningViolation):
            bound_text(
                "https://x/" + "a" * MAX_URL_LENGTH, "remote_url", MAX_URL_LENGTH
            )


class TestRoomPayload:
    def test_valid_room(self):
        payload = {
            "room_id": "voice_42",
            "participants": [
                {"id": "u1", "name": "A", "role": "owner"},
                {"id": "u2", "name": "B", "role": "member"},
            ],
        }
        cleaned = validate_room_payload(payload)
        assert len(cleaned["participants"]) == 2
        assert cleaned["participants"][0]["id"] == "u1"

    def test_roster_cap(self):
        payload = {
            "room_id": "r",
            "participants": [
                {"id": str(i), "name": "n", "role": "member"}
                for i in range(MAX_PARTICIPANTS_PER_ROOM + 1)
            ],
        }
        with pytest.raises(HardeningViolation):
            validate_room_payload(payload)

    def test_duplicate_participants_collapse(self):
        payload = {
            "room_id": "r",
            "participants": [{"id": "u1", "name": "A"}, {"id": "u1", "name": "A"}],
        }
        assert len(validate_room_payload(payload)["participants"]) == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"room_id": ""},
            {"room_id": "r", "participants": "not-a-list"},
            {"room_id": "r", "participants": [123]},
            {"room_id": "r", "participants": [{"role": "member"}]},
        ],
    )
    def test_malformed_room_rejected(self, payload):
        with pytest.raises(HardeningViolation):
            validate_room_payload(payload)


class TestManagerEntryPoints:
    def test_bad_ids_never_reach_db(self):
        class _DB:
            def __getattr__(self, _name):
                raise AssertionError("database must not be touched")

        manager = PlexiJoinManager(_DB())
        with pytest.raises(HardeningViolation):
            manager.get_connection(-1)
        with pytest.raises(HardeningViolation):
            manager.list_connections(page=0)
        with pytest.raises(HardeningViolation):
            manager.list_requests(per_page=10**9)
        with pytest.raises(HardeningViolation):
            manager.record_traffic(1, "sideways", 1)
