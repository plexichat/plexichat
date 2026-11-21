import utils.logger as logger
from ...base import SnowflakeID
from ..models import VoiceState
from ..exceptions import (
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    ChannelFullError,
    ChannelTypeError,
    PermissionDeniedError,
    UserNotInChannelError,
)


from .protocol import VoiceProtocol


class ModerationMixin(VoiceProtocol):
    def _require_can_moderate_member(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
    ) -> None:
        """Enforce role hierarchy before moderating another member.

        Holding ``voice.mute_members`` / ``voice.deafen_members`` /
        ``voice.move_members`` is not sufficient on its own: without a
        hierarchy check a moderator could mute, deafen, move or disconnect
        the server owner or a peer ranked at or above them. Mirror the
        servers module's kick/ban gate so voice moderation obeys the same
        member-target rules.

        A moderator targeting themselves is a no-op for hierarchy purposes;
        self-mute/self-deafen go through the ordinary self-service path.
        Fails closed when the servers module is not wired, since role rows
        cannot be read in that case.
        """
        if int(moderator_id) == int(target_user_id):
            return

        if not self._servers:
            raise PermissionDeniedError(
                "Cannot verify role hierarchy: servers module unavailable",
                permission,
            )

        # The owner id is the root of the hierarchy decision and changes only
        # on an explicit transfer. Read it through the bounded, db-scoped,
        # epoch-keyed L0 so a burst of moderation actions against one server
        # pays for a single authoritative read; a miss/error recomputes below,
        # and an ownership transfer bumps the epoch so the old id is retired.
        from src.core.servers.manager.server_owner_cache import (
            _MISS as _OWNER_MISS,
            get_cached_server_owner,
            prime_server_owner,
        )

        owner_id = get_cached_server_owner(self._db, server_id)
        if owner_id is _OWNER_MISS:
            server_row = self._db.fetch_one(
                "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
                (server_id,),
            )
            if not server_row:
                raise PermissionDeniedError("Server not found", permission)
            owner_id = server_row["owner_id"]
            # SECURITY: prime only a genuine integer id. ``prime_server_owner``
            # stores ``int(owner_id)``, so caching a malformed-but-coercible row
            # value ("05"->5, 5.9->5, True->1) would launder it into a genuine
            # int and re-open the owner grant on the next (cache-hit) call.
            if type(owner_id) is int:
                prime_server_owner(self._db, server_id, owner_id)

        # SECURITY: a projected srv_servers.owner_id vouches for the
        # moderator's/target's ownership only while it is a genuine integer
        # id. Coercing a malformed-but-coercible value with int() ("05"->5,
        # 5.9->5, True->1) would mark a non-owner as the server owner and let
        # them moderate anyone (owner bypasses role hierarchy). Fail closed:
        # type() also rejects bool.
        is_owner = type(owner_id) is int and owner_id == int(moderator_id)
        target_is_owner = type(owner_id) is int and owner_id == int(target_user_id)
        if target_is_owner:
            raise PermissionDeniedError("Cannot moderate the server owner", permission)

        # Imported lazily: the servers package wires core modules together at
        # import time, and this helper only needs the pure hierarchy predicate.
        from src.core.servers.permission_utils import can_manage_member

        user_roles = self._servers._get_member_role_rows(server_id, moderator_id)
        target_roles = self._servers._get_member_role_rows(server_id, target_user_id)
        if not can_manage_member(user_roles, target_roles, is_owner, target_is_owner):
            raise PermissionDeniedError(
                "Cannot moderate a member with equal or higher role", permission
            )

    def server_mute(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
        self._require_permission(moderator_id, server_id, "voice.mute_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        self._require_can_moderate_member(
            moderator_id, target_user_id, server_id, "voice.mute_members"
        )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET server_mute = 1, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )
        self._invalidate_voice_state(target_user_id)

        logger.debug(f"User {target_user_id} server muted by {moderator_id}")

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def server_unmute(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
        self._require_permission(moderator_id, server_id, "voice.mute_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        self._require_can_moderate_member(
            moderator_id, target_user_id, server_id, "voice.mute_members"
        )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET server_mute = 0, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )
        self._invalidate_voice_state(target_user_id)

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def server_deaf(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
        self._require_permission(moderator_id, server_id, "voice.deafen_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        self._require_can_moderate_member(
            moderator_id, target_user_id, server_id, "voice.deafen_members"
        )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET server_deaf = 1, server_mute = 1, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )
        self._invalidate_voice_state(target_user_id)

        logger.debug(f"User {target_user_id} server deafened by {moderator_id}")

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def server_undeaf(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> VoiceState:
        self._require_permission(moderator_id, server_id, "voice.deafen_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        self._require_can_moderate_member(
            moderator_id, target_user_id, server_id, "voice.deafen_members"
        )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE voice_states SET server_deaf = 0, last_activity = ? WHERE user_id = ?",
            (now, target_user_id),
        )
        self._invalidate_voice_state(target_user_id)

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def move_member(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        channel_id: SnowflakeID,
    ) -> VoiceState:
        channel = self._get_server_channel(channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        if not self._is_voice_channel(channel["channel_type"]):
            raise ChannelTypeError(
                "Not a voice channel",
                expected="voice or stage",
                actual=channel["channel_type"],
            )

        server_id = channel["server_id"]
        self._require_permission(moderator_id, server_id, "voice.move_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        # A moderator's authority is scoped to their own server. Without this
        # check, ``voice.move_members`` in server A could drag a user who is
        # connected to server B into A's channel (and silently reassign their
        # voice state's server_id).
        if state.server_id != server_id:
            raise ChannelAccessDeniedError(
                "User is not in this server's voice channel", "voice.move_members"
            )

        self._require_can_moderate_member(
            moderator_id, target_user_id, server_id, "voice.move_members"
        )

        # Agent 34 M3: moving a member into a channel they cannot connect to
        # (or that is full) must fail just like a normal join_channel.
        self._require_permission(target_user_id, server_id, "voice.connect", channel_id)

        settings = self._get_channel_settings(channel_id)
        user_limit = settings.get("user_limit", 0)
        if user_limit > 0 and state.channel_id != channel_id:
            current_count = self._get_channel_user_count(channel_id)
            if current_count >= user_limit:
                raise ChannelFullError(
                    f"Channel is full ({current_count}/{user_limit})",
                    limit=user_limit,
                    current=current_count,
                )

        # Scope the cleanup to the channel being LEFT so pending speaker
        # requests in unrelated voice channels are preserved.
        old_channel_id = state.channel_id
        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE user_id = ? AND channel_id = ?",
            (target_user_id, old_channel_id),
        )

        now = self._get_timestamp()
        suppress = channel["channel_type"] == "stage"

        self._db.execute(
            """UPDATE voice_states
               SET channel_id = ?, server_id = ?, suppress = ?, last_activity = ?
               WHERE user_id = ?""",
            (channel_id, server_id, 1 if suppress else 0, now, target_user_id),
        )
        self._invalidate_voice_state(target_user_id)

        # A moderation move is a leave+join: fire the same call-lifecycle
        # hooks so the old channel's call is ended (and its participant
        # snapshot pruned) and the new channel's call starts. Run them AFTER
        # the UPDATE so each hook observes the new membership.
        if old_channel_id != channel_id:
            self._on_member_left(target_user_id, old_channel_id)
            self._on_member_joined(target_user_id, channel_id)

        logger.debug(
            f"User {target_user_id} moved to channel {channel_id} by {moderator_id}"
        )

        result = self.get_voice_state(target_user_id)
        assert result is not None
        return result

    def disconnect_member(
        self,
        moderator_id: SnowflakeID,
        target_user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> bool:
        self._require_permission(moderator_id, server_id, "voice.move_members")

        state = self.get_voice_state(target_user_id)
        if not state:
            raise UserNotInChannelError(
                f"User {target_user_id} is not in a voice channel"
            )

        if state.server_id != server_id:
            raise ChannelAccessDeniedError("User is not in this server's voice channel")

        self._require_can_moderate_member(
            moderator_id, target_user_id, server_id, "voice.move_members"
        )

        self._db.execute(
            "DELETE FROM voice_speaker_requests WHERE user_id = ?", (target_user_id,)
        )

        self._db.execute(
            "DELETE FROM voice_states WHERE user_id = ?", (target_user_id,)
        )
        self._invalidate_voice_state(target_user_id)

        # Mirror leave_channel: without this the moderation teardown skips the
        # call-lifecycle hook, so disconnecting the last member left the
        # channel's call active forever (duration growing, no transcription)
        # and a later rejoin was seen as an existing membership, bypassing the
        # recording-consent gate. Run it after the DELETEs so the channel
        # already reports the user as gone.
        self._on_member_left(target_user_id, state.channel_id)

        logger.debug(f"User {target_user_id} disconnected from voice by {moderator_id}")

        return True
