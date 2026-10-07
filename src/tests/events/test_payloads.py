"""Tests for event payload creation."""

from src.core import events
from src.core.events.types import EventType


class TestMessagePayloads:
    """Tests for message event payloads."""

    def test_create_message_create(self):
        """Test MESSAGE_CREATE payload creation."""
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
        )
        assert event.event_type == EventType.MESSAGE_CREATE
        assert event.message_id == 123
        assert event.channel_id == 456
        assert event.content == "Hello!"
        assert event.data["id"] == "123"
        assert event.data["channel_id"] == "456"
        assert event.data["content"] == "Hello!"

    def test_create_message_create_with_server(self):
        """Test MESSAGE_CREATE with server ID."""
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
            server_id=111,
        )
        assert event.server_id == 111
        assert event.data["guild_id"] == "111"

    def test_create_message_create_with_attachments(self):
        """Test MESSAGE_CREATE with attachments."""
        attachments = [{"id": "1", "filename": "test.png"}]
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Check this out",
            attachments=attachments,
        )
        assert event.attachments == attachments
        assert event.data["attachments"] == attachments

    def test_create_message_update(self):
        """Test MESSAGE_UPDATE payload creation."""
        event = events.create_message_update(
            message_id=123,
            channel_id=456,
            content="Updated content",
        )
        assert event.event_type == EventType.MESSAGE_UPDATE
        assert event.message_id == 123
        assert event.data["id"] == "123"
        assert event.data["content"] == "Updated content"
        assert "edited_timestamp" in event.data

    def test_create_message_delete(self):
        """Test MESSAGE_DELETE payload creation."""
        event = events.create_message_delete(
            message_id=123,
            channel_id=456,
        )
        assert event.event_type == EventType.MESSAGE_DELETE
        assert event.message_id == 123
        assert event.data["id"] == "123"
        assert event.data["channel_id"] == "456"


class TestPresencePayloads:
    """Tests for presence event payloads."""

    def test_create_presence_update(self):
        """Test PRESENCE_UPDATE payload creation."""
        event = events.create_presence_update(
            user_id=123,
            status="online",
        )
        assert event.event_type == EventType.PRESENCE_UPDATE
        assert event.user_id == 123
        assert event.status == "online"
        assert event.data["status"] == "online"
        assert event.data["user"]["id"] == "123"

    def test_create_presence_update_with_activities(self):
        """Test PRESENCE_UPDATE with activities."""
        activities = [{"type": 0, "name": "Gaming"}]
        event = events.create_presence_update(
            user_id=123,
            status="online",
            activities=activities,
        )
        assert event.activities == activities
        assert event.data["activities"] == activities

    def test_create_typing_start(self):
        """Test TYPING_START payload creation."""
        event = events.create_typing_start(
            user_id=123,
            channel_id=456,
        )
        assert event.event_type == EventType.TYPING_START
        assert event.user_id == 123
        assert event.channel_id == 456
        assert event.data["user_id"] == "123"
        assert event.data["channel_id"] == "456"
        # Timestamps are Unix milliseconds (same contract as REST and the
        # other gateway live events): the payload value must match the
        # event timestamp instead of truncating to seconds.
        assert event.data["timestamp"] == event.timestamp
        assert event.data["timestamp"] >= 1_700_000_000_000

    def test_create_typing_start_with_server(self):
        """Test TYPING_START with server ID."""
        event = events.create_typing_start(
            user_id=123,
            channel_id=456,
            server_id=789,
        )
        assert event.server_id == 789
        assert event.data["guild_id"] == "789"


class TestChannelPayloads:
    """Tests for channel event payloads."""

    def test_create_channel_create(self):
        """Test CHANNEL_CREATE payload creation."""
        event = events.create_channel_create(
            channel_id=123,
            channel_type=0,
            name="general",
        )
        assert event.event_type == EventType.CHANNEL_CREATE
        assert event.channel_id == 123
        assert event.name == "general"
        assert event.data["id"] == "123"
        assert event.data["type"] == 0
        assert event.data["name"] == "general"

    def test_create_channel_update(self):
        """Test CHANNEL_UPDATE payload creation."""
        event = events.create_channel_update(
            channel_id=123,
            channel_type=0,
            name="renamed",
            topic="New topic",
        )
        assert event.event_type == EventType.CHANNEL_UPDATE
        assert event.name == "renamed"
        assert event.topic == "New topic"
        assert event.data["topic"] == "New topic"

    def test_create_channel_delete(self):
        """Test CHANNEL_DELETE payload creation."""
        event = events.create_channel_delete(
            channel_id=123,
            channel_type=0,
        )
        assert event.event_type == EventType.CHANNEL_DELETE
        assert event.data["id"] == "123"


class TestGuildPayloads:
    """Tests for guild event payloads."""

    def test_create_guild_create(self):
        """Test GUILD_CREATE payload creation."""
        event = events.create_guild_create(
            server_id=123,
            name="Test Server",
            owner_id=456,
            member_count=10,
        )
        assert event.event_type == EventType.GUILD_CREATE
        assert event.server_id == 123
        assert event.name == "Test Server"
        assert event.owner_id == 456
        assert event.member_count == 10
        assert event.data["id"] == "123"
        assert event.data["name"] == "Test Server"

    def test_create_guild_update(self):
        """Test GUILD_UPDATE payload creation."""
        event = events.create_guild_update(
            server_id=123,
            name="Updated Name",
        )
        assert event.event_type == EventType.GUILD_UPDATE
        assert event.data["id"] == "123"
        assert event.data["name"] == "Updated Name"

    def test_create_guild_delete(self):
        """Test GUILD_DELETE payload creation."""
        event = events.create_guild_delete(server_id=123)
        assert event.event_type == EventType.GUILD_DELETE
        assert event.data["id"] == "123"


class TestMemberPayloads:
    """Tests for guild member event payloads."""

    def test_create_guild_member_add(self):
        """Test GUILD_MEMBER_ADD payload creation."""
        event = events.create_guild_member_add(
            server_id=123,
            user_id=456,
        )
        assert event.event_type == EventType.GUILD_MEMBER_ADD
        assert event.server_id == 123
        assert event.member_user_id == 456
        assert event.data["guild_id"] == "123"
        assert event.data["user"]["id"] == "456"

    def test_create_guild_member_add_with_nick(self):
        """Test GUILD_MEMBER_ADD with nickname."""
        event = events.create_guild_member_add(
            server_id=123,
            user_id=456,
            nick="TestNick",
        )
        assert event.nick == "TestNick"
        assert event.data["nick"] == "TestNick"

    def test_create_guild_member_remove(self):
        """Test GUILD_MEMBER_REMOVE payload creation."""
        event = events.create_guild_member_remove(
            server_id=123,
            user_id=456,
        )
        assert event.event_type == EventType.GUILD_MEMBER_REMOVE
        assert event.data["guild_id"] == "123"
        assert event.data["user"]["id"] == "456"

    def test_create_guild_member_update(self):
        """Test GUILD_MEMBER_UPDATE payload creation."""
        event = events.create_guild_member_update(
            server_id=123,
            user_id=456,
            nick="NewNick",
            roles=[1, 2, 3],
        )
        assert event.event_type == EventType.GUILD_MEMBER_UPDATE
        assert event.nick == "NewNick"
        assert event.roles == [1, 2, 3]


class TestVoicePayloads:
    """Tests for voice event payloads."""

    def test_create_voice_state_update(self):
        """Test VOICE_STATE_UPDATE payload creation."""
        event = events.create_voice_state_update(
            user_id=123,
            channel_id=456,
            server_id=789,
        )
        assert event.event_type == EventType.VOICE_STATE_UPDATE
        assert event.user_id == 123
        assert event.voice_channel_id == 456
        assert event.data["user_id"] == "123"
        assert event.data["channel_id"] == "456"

    def test_create_voice_state_update_muted(self):
        """Test VOICE_STATE_UPDATE with mute flags."""
        event = events.create_voice_state_update(
            user_id=123,
            channel_id=456,
            self_mute=True,
            self_deaf=True,
        )
        assert event.self_mute is True
        assert event.self_deaf is True
        assert event.data["self_mute"] is True
        assert event.data["self_deaf"] is True

    def test_create_voice_state_update_disconnect(self):
        """Test VOICE_STATE_UPDATE for disconnect."""
        event = events.create_voice_state_update(
            user_id=123,
            channel_id=None,
        )
        assert event.voice_channel_id is None
        assert event.data["channel_id"] is None


class TestReactionPayloads:
    """Tests for reaction event payloads."""

    def test_create_reaction_add(self):
        """Test MESSAGE_REACTION_ADD payload creation."""
        emoji = {"name": "thumbsup", "id": None}
        event = events.create_reaction_add(
            user_id=123,
            message_id=456,
            channel_id=789,
            emoji=emoji,
        )
        assert event.event_type == EventType.MESSAGE_REACTION_ADD
        assert event.user_id == 123
        assert event.message_id == 456
        assert event.emoji == emoji
        assert event.data["emoji"] == emoji

    def test_create_reaction_remove(self):
        """Test MESSAGE_REACTION_REMOVE payload creation."""
        emoji = {"name": "thumbsup", "id": None}
        event = events.create_reaction_remove(
            user_id=123,
            message_id=456,
            channel_id=789,
            emoji=emoji,
        )
        assert event.event_type == EventType.MESSAGE_REACTION_REMOVE
        assert event.data["user_id"] == "123"


class TestEventToDict:
    """Tests for event serialization."""

    def test_event_to_dict(self):
        """Test event to_dict method."""
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
        )
        result = event.to_dict()
        assert "t" in result
        assert "d" in result
        assert result["t"] == "MESSAGE_CREATE"
        assert isinstance(result["d"], dict)

    def test_event_data_has_string_ids(self):
        """Test that IDs in event data are strings."""
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
        )
        data = event.data
        assert isinstance(data["id"], str)
        assert isinstance(data["channel_id"], str)


class TestChannelPinsUpdatePayload:
    """CHANNEL_PINS_UPDATE carries string IDs like the message family."""

    def test_pins_update_stringifies_ids(self, monkeypatch):
        from src.core.events import gateway_emit

        captured = {}

        def fake_emit(event_type, data, **kwargs):
            captured["event_type"] = event_type
            captured["data"] = data
            captured["kwargs"] = kwargs

        monkeypatch.setattr(gateway_emit, "_emit", fake_emit)
        gateway_emit.emit_channel_pins_update(
            123, guild_id=456, pinned_message_ids=[7, 8]
        )

        assert captured["event_type"] == EventType.CHANNEL_PINS_UPDATE
        data = captured["data"]
        assert data["channel_id"] == "123"
        assert data["server_id"] == "456"
        assert data["guild_id"] == "456"
        assert data["pinned_message_ids"] == ["7", "8"]
        # Routing keys stay ints, as with the sibling emitters.
        assert captured["kwargs"]["channel_id"] == 123
        assert captured["kwargs"]["server_id"] == 456

    def test_pins_update_without_guild_or_pins(self, monkeypatch):
        from src.core.events import gateway_emit

        captured = {}

        def fake_emit(event_type, data, **kwargs):
            captured["data"] = data

        monkeypatch.setattr(gateway_emit, "_emit", fake_emit)
        gateway_emit.emit_channel_pins_update(123)

        data = captured["data"]
        assert data["channel_id"] == "123"
        assert data["server_id"] is None
        assert data["guild_id"] is None
        assert data["pinned_message_ids"] == []


class TestWorkbookUpdatePayload:
    """WORKBOOK_UPDATE always carries its identifiers from the arguments."""

    def test_workbook_update_injects_ids(self, monkeypatch):
        from src.core.events import gateway_emit

        captured = {}

        def fake_emit(event_type, data, **kwargs):
            captured["event_type"] = event_type
            captured["data"] = data
            captured["kwargs"] = kwargs

        monkeypatch.setattr(gateway_emit, "_emit", fake_emit)
        gateway_emit.emit_workbook_update(
            42,
            7,
            99,
            {"note": "hello"},
            user_ids=[1, 2],
            exclude_user_ids=[99],
        )

        assert captured["event_type"] == EventType.WORKBOOK_UPDATE
        data = captured["data"]
        assert data["workbook_id"] == "42"
        assert data["revision"] == 7
        assert data["actor_id"] == "99"
        assert data["note"] == "hello"
        assert captured["kwargs"]["user_ids"] == [1, 2]
        assert captured["kwargs"]["exclude_user_ids"] == [99]

    def test_workbook_update_overrides_stale_ids_and_keeps_caller_dict(
        self, monkeypatch
    ):
        from src.core.events import gateway_emit

        captured = {}

        def fake_emit(event_type, data, **kwargs):
            captured["data"] = data

        monkeypatch.setattr(gateway_emit, "_emit", fake_emit)
        extra = {"workbook_id": "stale", "revision": 1, "actor_id": "stale"}
        gateway_emit.emit_workbook_update(42, 7, 99, extra)

        data = captured["data"]
        assert data["workbook_id"] == "42"
        assert data["revision"] == 7
        assert data["actor_id"] == "99"
        # The caller's dict is not mutated.
        assert extra == {"workbook_id": "stale", "revision": 1, "actor_id": "stale"}

    def test_workbook_update_stringifies_nested_id_extras(self, monkeypatch):
        """Nested snowflakes inside ``data`` extras must not leak as numbers."""
        from src.core.events import gateway_emit

        captured = {}

        def fake_emit(event_type, data, **kwargs):
            captured["data"] = data

        monkeypatch.setattr(gateway_emit, "_emit", fake_emit)
        extra = {
            "kind": "comment",
            "comment": {"id": 1234567890123456789, "row": 3},
            "mentioned_user_ids": [1234567890123456789],
            "like_count": 5,
        }
        gateway_emit.emit_workbook_update(42, 7, 99, extra)

        data = captured["data"]
        assert data["comment"] == {"id": "1234567890123456789", "row": 3}
        assert data["mentioned_user_ids"] == ["1234567890123456789"]
        assert data["like_count"] == 5
        assert data["revision"] == 7
        # The caller's dict is not mutated.
        assert extra["comment"] == {"id": 1234567890123456789, "row": 3}


class TestUserUpdatePayload:
    """USER_UPDATE must carry snowflake ids as strings."""

    def test_user_update_stringifies_ids(self, monkeypatch):
        from src.core.events import gateway_emit

        captured = {}

        def fake_emit(event_type, data, **kwargs):
            captured["event_type"] = event_type
            captured["data"] = data
            captured["kwargs"] = kwargs

        monkeypatch.setattr(gateway_emit, "_emit", fake_emit)
        gateway_emit.emit_user_update(
            {"id": 1234567890123456789, "username": "alice"},
            exclude_user_id=42,
        )

        assert captured["event_type"] == EventType.USER_UPDATE
        assert captured["data"]["user"]["id"] == "1234567890123456789"
        assert captured["data"]["user"]["username"] == "alice"
        # Routing keys stay ints, matching the sibling emitters.
        assert captured["kwargs"]["user_ids"] == [1234567890123456789]
        assert captured["kwargs"]["exclude_user_ids"] == [42]


def test_user_update_does_not_mutate_caller_dict(monkeypatch):
    from src.core.events import gateway_emit

    monkeypatch.setattr(gateway_emit, "_emit", lambda *a, **k: None)
    user = {"id": 1234567890123456789, "username": "alice"}
    gateway_emit.emit_user_update(user)
    assert user["id"] == 1234567890123456789


class TestNonRaisingEmitters:
    """Emitters must never raise into the REST handler (module contract)."""

    def test_user_update_with_malformed_id_does_not_raise(self):
        from src.core.events import gateway_emit

        # int("abc") used to propagate ValueError out of the helper.
        assert gateway_emit.emit_user_update({"id": "abc"}) is None

    def test_workbook_update_with_malformed_revision_does_not_raise(self):
        from src.core.events import gateway_emit

        assert gateway_emit.emit_workbook_update(42, "not-a-number", 99, {}) is None

    def test_broken_payload_build_does_not_raise(self, monkeypatch):
        from src.core.events import gateway_emit

        class BadStr:
            def __str__(self):
                raise RuntimeError("boom")

        # str() coercion inside the helper must also stay non-raising.
        assert gateway_emit.emit_channel_pins_update(BadStr()) is None


class TestExplicitZeroTimestamps:
    """Explicit ``0`` timestamps must survive instead of falling back to now.

    The payload factories used ``value or now`` for their optional timestamp
    defaults, so a caller that passed ``0`` (epoch, or a backfill sentinel)
    silently received the current clock instead. The flat message builder
    (``message_events.build_flat_message_create``) already guards with
    ``is not None``; these tests pin the same contract on the
    Discord-style factories.
    """

    def test_message_create_preserves_zero_timestamp(self):
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
            timestamp=0,
        )
        assert event.timestamp == 0
        assert event.data["timestamp"] == 0

    def test_message_update_preserves_zero_edited_timestamp(self):
        event = events.create_message_update(
            message_id=123,
            channel_id=456,
            content="Updated content",
            edited_timestamp=0,
        )
        assert event.edited_timestamp == 0
        assert event.data["edited_timestamp"] == 0

    def test_guild_member_add_preserves_zero_joined_at(self):
        event = events.create_guild_member_add(
            server_id=123,
            user_id=456,
            joined_at=0,
        )
        assert event.joined_at == 0
        assert event.data["joined_at"] == 0


class TestExplicitZeroIds:
    """Explicit ``0`` ids must survive instead of being treated as absent.

    The payload factories guarded their optional id scope keys with truthiness
    (``if server_id:``), so a caller that passed ``0`` silently lost the
    scope keys from ``data``. The sibling ``gateway_emit`` helpers already
    guard with ``is not None``; these tests pin the same contract on the
    Discord-style factories.
    """

    def test_message_create_preserves_zero_server_id(self):
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["server_id"] == "0"
        assert event.data["guild_id"] == "0"

    def test_message_update_preserves_zero_server_id(self):
        event = events.create_message_update(
            message_id=123,
            channel_id=456,
            content="Updated content",
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["server_id"] == "0"
        assert event.data["guild_id"] == "0"

    def test_message_delete_preserves_zero_server_id(self):
        event = events.create_message_delete(
            message_id=123,
            channel_id=456,
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["server_id"] == "0"
        assert event.data["guild_id"] == "0"

    def test_presence_update_preserves_zero_server_id(self):
        event = events.create_presence_update(
            user_id=123,
            status="online",
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["guild_id"] == "0"

    def test_typing_start_preserves_zero_server_id(self):
        event = events.create_typing_start(
            user_id=123,
            channel_id=456,
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["guild_id"] == "0"

    def test_channel_create_preserves_zero_ids(self):
        event = events.create_channel_create(
            channel_id=123,
            channel_type=0,
            server_id=0,
            parent_id=0,
        )
        assert event.server_id == 0
        assert event.parent_id == 0
        assert event.data["guild_id"] == "0"
        assert event.data["parent_id"] == "0"

    def test_channel_update_preserves_zero_ids(self):
        event = events.create_channel_update(
            channel_id=123,
            channel_type=0,
            server_id=0,
            parent_id=0,
        )
        assert event.server_id == 0
        assert event.parent_id == 0
        assert event.data["guild_id"] == "0"
        assert event.data["parent_id"] == "0"

    def test_channel_delete_preserves_zero_server_id(self):
        event = events.create_channel_delete(
            channel_id=123,
            channel_type=0,
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["guild_id"] == "0"

    def test_guild_update_preserves_zero_owner_id(self):
        event = events.create_guild_update(
            server_id=123,
            owner_id=0,
        )
        assert event.owner_id == 0
        assert event.data["owner_id"] == "0"

    def test_guild_member_add_preserves_empty_nick(self):
        event = events.create_guild_member_add(
            server_id=123,
            user_id=456,
            nick="",
        )
        assert event.nick == ""
        assert event.data["nick"] == ""

    def test_voice_state_update_preserves_zero_server_id(self):
        event = events.create_voice_state_update(
            user_id=123,
            channel_id=456,
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["guild_id"] == "0"

    def test_reaction_add_preserves_zero_server_id(self):
        event = events.create_reaction_add(
            user_id=123,
            message_id=456,
            channel_id=789,
            emoji={"name": "thumbsup", "id": None},
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["guild_id"] == "0"

    def test_reaction_remove_preserves_zero_server_id(self):
        event = events.create_reaction_remove(
            user_id=123,
            message_id=456,
            channel_id=789,
            emoji={"name": "thumbsup", "id": None},
            server_id=0,
        )
        assert event.server_id == 0
        assert event.data["guild_id"] == "0"


class TestExplicitEmptyClears:
    """Explicit ``""`` clears must survive instead of being dropped.

    The GUILD_UPDATE factory guarded ``name``/``icon`` with truthiness,
    so a caller that passed ``""`` (e.g. clearing a server icon) silently
    produced a payload without the key. ``gateway_emit.emit_guild_update``
    already guards with ``is not None`` for exactly this reason; these
    tests pin the same contract on the payload factory.
    """

    def test_guild_update_preserves_empty_name(self):
        event = events.create_guild_update(
            server_id=123,
            name="",
        )
        assert event.name == ""
        assert event.data["name"] == ""

    def test_guild_update_preserves_empty_icon(self):
        event = events.create_guild_update(
            server_id=123,
            icon="",
        )
        assert event.icon == ""
        assert event.data["icon"] == ""

    def test_guild_update_omits_unset_name_and_icon(self):
        event = events.create_guild_update(
            server_id=123,
        )
        assert "name" not in event.data
        assert "icon" not in event.data

    def test_channel_update_preserves_empty_name(self):
        event = events.create_channel_update(
            channel_id=123,
            channel_type=0,
            name="",
        )
        assert event.name == ""
        assert event.data["name"] == ""

    def test_channel_update_preserves_empty_topic(self):
        event = events.create_channel_update(
            channel_id=123,
            channel_type=0,
            topic="",
        )
        assert event.topic == ""
        assert event.data["topic"] == ""

    def test_channel_update_omits_unset_name_and_topic(self):
        event = events.create_channel_update(
            channel_id=123,
            channel_type=0,
        )
        assert "name" not in event.data
        assert "topic" not in event.data
