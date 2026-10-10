"""
Tests for the voice call lifecycle glue (CallLifecycleMixin).

A "call" is an aggregate over a voice channel: started by the first
participant, ended by the last, persisted through the artifacts
``VoiceCallManager``. These tests isolate the mixin with stub voice
state and a fake call manager so no real signaling is involved.
"""

import pytest
from unittest.mock import patch

from src.core.voice.manager.calls import CallLifecycleMixin


class _VoiceState:
    def __init__(self, server_id):
        self.server_id = server_id


class _FakeCall:
    def __init__(
        self,
        call_id,
        channel_id,
        server_id=None,
        initiator_id=None,
        conversation_id=None,
    ):
        self.id = call_id
        self.channel_id = channel_id
        self.server_id = server_id
        self.initiator_id = initiator_id
        self.conversation_id = conversation_id
        self.duration_seconds = 37


class _FakeCallManager:
    def __init__(self):
        self.active = {}
        self.participants = {}
        self.ended = []
        self.started = []

    def get_active_by_channel(self, channel_id):
        return self.active.get(channel_id)

    def start_call(self, **kwargs):
        call = _FakeCall(call_id=kwargs["channel_id"], **kwargs)
        self.active[kwargs["channel_id"]] = call
        self.started.append(call)
        # Mirrors VoiceCallManager.start_call: the initiator is the first
        # participant (participant_count=1) and the live membership is
        # snapshotted into the participant roster.
        self.participants.setdefault(call.id, set()).add(kwargs["initiator_id"])
        return call

    def add_participant(self, call_id, user_id):
        self.participants.setdefault(call_id, set()).add(user_id)

    def remove_participant(self, call_id, user_id):
        self.participants.setdefault(call_id, set()).discard(user_id)

    def end_call(self, call_id):
        call = self.active.pop(call_id, None)
        if call is not None:
            self.ended.append(call)
        return call


class _StubVoice(CallLifecycleMixin):
    """Minimal VoiceProtocol surface used by the lifecycle mixin."""

    def __init__(self, db, call_manager=None):
        self._db = db
        self._voice_call_manager = call_manager
        self.state = None
        self.members = []
        self.user_count = 0

    def get_voice_state(self, user_id):
        return self.state

    def get_channel_members(self, channel_id):
        return self.members

    def _get_channel_user_count(self, channel_id):
        return self.user_count


@pytest.fixture
def voice_channel(test_server, server_manager):
    """A voice channel plus its owner."""
    server, user = test_server
    channel = server_manager.create_channel(
        user.id, server.id, "voice", channel_type=server_manager.ChannelType.VOICE
    )
    return server, user, channel


@pytest.fixture
def voice_stub(db):
    return _StubVoice(db)


class TestSetCallManager:
    def test_set_voice_call_manager_stores(self, voice_stub):
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        assert voice_stub._voice_call_manager is manager

    def test_set_none_disables_recording(self, voice_stub):
        voice_stub.set_voice_call_manager(None)
        assert voice_stub._voice_call_manager is None


class TestMemberJoin:
    def test_join_starts_call_when_no_active_call(self, voice_stub, voice_channel, db):
        server, user, channel = voice_channel
        db.execute(
            "UPDATE srv_channels SET conversation_id = ? WHERE id = ?",
            (555, channel.id),
        )
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        voice_stub.state = _VoiceState(server_id=server.id)

        voice_stub._on_member_joined(user.id, channel.id)

        assert len(manager.started) == 1
        call = manager.started[0]
        assert call.channel_id == channel.id
        assert call.server_id == server.id
        assert call.initiator_id == user.id
        assert call.conversation_id == 555
        assert call.id in manager.participants

    def test_join_without_server_state_sets_none_server(
        self, voice_stub, voice_channel
    ):
        server, user, channel = voice_channel
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        voice_stub.state = None

        voice_stub._on_member_joined(user.id, channel.id)

        assert manager.started[0].server_id is None
        assert manager.started[0].conversation_id is None

    def test_join_adds_participant_to_active_call(self, voice_stub, voice_channel):
        server, user, channel = voice_channel
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        voice_stub.state = _VoiceState(server_id=server.id)

        voice_stub._on_member_joined(user.id, channel.id)
        second_user = user.id + 1
        voice_stub._on_member_joined(second_user, channel.id)

        assert len(manager.started) == 1
        call = manager.active[channel.id]
        assert {user.id, second_user} <= manager.participants[call.id]

    def test_join_without_manager_is_noop(self, voice_stub, voice_channel):
        server, user, channel = voice_channel
        voice_stub._on_member_joined(user.id, channel.id)  # must not raise

    def test_join_call_manager_error_swallowed(self, voice_stub, voice_channel):
        server, user, channel = voice_channel
        manager = _FakeCallManager()

        def boom(**kwargs):
            raise RuntimeError("call manager down")

        manager.start_call = boom
        voice_stub.set_voice_call_manager(manager)
        voice_stub._on_member_joined(user.id, channel.id)  # must not raise


class TestConversationResolution:
    def test_resolves_conversation_id(self, voice_stub, voice_channel, db):
        server, user, channel = voice_channel
        db.execute(
            "UPDATE srv_channels SET conversation_id = ? WHERE id = ?",
            (777, channel.id),
        )
        assert voice_stub._resolve_conversation_for_channel(channel.id) == 777

    def test_returns_none_without_conversation(self, voice_stub, voice_channel):
        server, user, channel = voice_channel
        assert voice_stub._resolve_conversation_for_channel(channel.id) is None

    def test_returns_none_for_unknown_channel(self, voice_stub):
        assert voice_stub._resolve_conversation_for_channel(999999) is None


class TestMemberLeave:
    def test_leave_without_active_call_is_noop(self, voice_stub, voice_channel):
        server, user, channel = voice_channel
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        voice_stub._on_member_left(user.id, channel.id)
        assert manager.ended == []

    def test_leave_with_remaining_members_keeps_call(self, voice_stub, voice_channel):
        server, user, channel = voice_channel
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        voice_stub.state = _VoiceState(server_id=server.id)
        voice_stub._on_member_joined(user.id, channel.id)
        other = user.id + 1
        voice_stub._on_member_joined(other, channel.id)

        voice_stub.members = [user.id, other]  # one member remains after leave
        voice_stub._on_member_left(other, channel.id)

        call = manager.active[channel.id]
        assert other not in manager.participants[call.id]
        assert manager.ended == []

    def test_leave_last_member_ends_call(self, voice_stub, voice_channel):
        server, user, channel = voice_channel
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        voice_stub.state = _VoiceState(server_id=server.id)
        voice_stub._on_member_joined(user.id, channel.id)

        voice_stub.members = []
        voice_stub._on_member_left(user.id, channel.id)

        assert len(manager.ended) == 1
        assert manager.ended[0].channel_id == channel.id
        assert channel.id not in manager.active

    def test_leave_last_member_with_inconsistent_count_keeps_call(
        self, voice_stub, voice_channel
    ):
        server, user, channel = voice_channel
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        voice_stub.state = _VoiceState(server_id=server.id)
        voice_stub._on_member_joined(user.id, channel.id)

        # get_channel_members reports 0 but the user-count query reports
        # live members: the guard must skip end_call.
        voice_stub.members = []
        voice_stub.user_count = 1
        voice_stub._on_member_left(user.id, channel.id)

        assert manager.ended == []
        assert channel.id in manager.active


class TestTranscriptionScheduling:
    def _start_call(self, voice_stub, voice_channel):
        server, user, channel = voice_channel
        manager = _FakeCallManager()
        voice_stub.set_voice_call_manager(manager)
        voice_stub.state = _VoiceState(server_id=server.id)
        voice_stub._on_member_joined(user.id, channel.id)
        voice_stub.members = []
        return user, channel, manager

    def test_not_scheduled_when_disabled(self, voice_stub, voice_channel):
        import utils.config as config

        config.set(
            "artifacts",
            {
                "voice": {
                    "transcription": {
                        "enabled": False,
                        "auto_transcribe": True,
                    }
                }
            },
        )
        user, channel, manager = self._start_call(voice_stub, voice_channel)
        with patch(
            "src.core.artifacts.transcription.worker.schedule_transcribe_call"
        ) as mock_schedule:
            voice_stub._on_member_left(user.id, channel.id)
            mock_schedule.assert_not_called()

    def test_not_scheduled_when_auto_transcribe_off(self, voice_stub, voice_channel):
        import utils.config as config

        config.set(
            "artifacts",
            {
                "voice": {
                    "transcription": {
                        "enabled": True,
                        "auto_transcribe": False,
                    }
                }
            },
        )
        user, channel, manager = self._start_call(voice_stub, voice_channel)
        with patch(
            "src.core.artifacts.transcription.worker.schedule_transcribe_call"
        ) as mock_schedule:
            voice_stub._on_member_left(user.id, channel.id)
            mock_schedule.assert_not_called()

    def test_scheduled_when_enabled_and_auto(self, voice_stub, voice_channel):
        import utils.config as config

        config.set(
            "artifacts",
            {
                "voice": {
                    "transcription": {
                        "enabled": True,
                        "auto_transcribe": True,
                    }
                }
            },
        )
        user, channel, manager = self._start_call(voice_stub, voice_channel)
        with patch(
            "src.core.artifacts.transcription.worker.schedule_transcribe_call"
        ) as mock_schedule:
            voice_stub._on_member_left(user.id, channel.id)
            mock_schedule.assert_called_once()
            call_id = mock_schedule.call_args.args[0]
            assert call_id == channel.id
