"""
Voice handlers - Handle voice-related opcodes (voice state, WebRTC signaling).
"""

from typing import Optional, Dict, Any, Tuple, List, TYPE_CHECKING

import utils.logger as logger
from starlette.concurrency import run_in_threadpool

from src.api.websocket.connection import Connection
from src.api.websocket.opcodes import GatewayCloseCode, GatewayOpcode

if TYPE_CHECKING:
    from src.core.servers.manager import ServerManager


def _clamp_volume(raw: Any) -> float:
    """Coerce a client-reported level into a renderable ``0.0``-``1.0`` float.

    The value is forwarded verbatim to every other member of the channel, so a
    string, NaN or an out-of-range number must not reach their UI (the client
    multiplies it into a CSS width). Anything unusable degrades to silence.
    """
    try:
        volume = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if volume != volume:  # NaN
        return 0.0
    if volume == float("inf") or volume == float("-inf"):
        return 0.0
    return max(0.0, min(volume, 1.0))


class VoiceHandler:
    """Handles voice-related opcodes."""

    def __init__(self, servers_module: Optional["ServerManager"] = None):
        """
        Initialize the voice handler.

        Args:
            servers_module: Servers module for guild data
        """
        self._servers: Optional["ServerManager"] = servers_module

    async def handle_voice_state_update(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice state update opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        channel_id = data.get("channel_id")
        self_mute = data.get("self_mute", False)
        self_deaf = data.get("self_deaf", False)
        self_video = data.get("self_video", False)
        self_stream = data.get("self_stream", False)
        # Recording consent rides on this opcode: the client's dialog answers
        # ``consented: true`` for yes and ``consented: false`` for no. The
        # field used to be dropped on the floor while
        # ``VoiceCallManager.add_consent`` -- the only writer of
        # ``consented_participants`` -- had no caller, so a recording could
        # never satisfy its every-participant consent gate. A false is a real
        # answer too: it revokes a previously given consent, and a recording
        # that loses one participant's consent is stopped rather than kept.
        # Absent/unknown values are not a decision and change nothing.
        consented_raw = data.get("consented")
        if consented_raw is True or consented_raw == "true":
            consented: Optional[bool] = True
        elif consented_raw is False or consented_raw == "false":
            consented = False
        else:
            consented = None

        if channel_id is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid channel_id type in voice state update: {type(channel_id)}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            from src.core import voice

            voice_state = voice.update_voice_state(
                user_id=connection.user_id,
                self_mute=self_mute,
                self_deaf=self_deaf,
                streaming=self_stream,
                video=self_video,
            )

            # SECURITY (Agent 34 M2): derive fan-out from the AUTHORITATIVE
            # DB state's channel. Trusting the client-supplied channel_id let
            # a user in channel A spoof membership events into channel B's
            # viewer set (information disclosure about B).
            authoritative_channel_id = getattr(voice_state, "channel_id", None) or (
                channel_id
            )

            await self._dispatch_voice_state_update(
                user_id=connection.user_id,
                channel_id_for_recipients=authoritative_channel_id,
                event_channel_id=authoritative_channel_id,
                voice_state=voice_state,
            )

            if consented is not None:
                # Delivery must not fail the state update it arrived with:
                # both helpers report False instead of raising, and a consent
                # that cannot be persisted leaves the recording gated shut
                # (an unrecorded grant never opens it, an unrecorded revoke
                # never closes it -- the call manager re-checks membership
                # and every live participant's consent before recording).
                try:
                    handler_name = (
                        "record_call_consent" if consented else "revoke_call_consent"
                    )
                    handler = getattr(voice, handler_name, None)
                    if callable(handler):
                        handler(connection.user_id, authoritative_channel_id)
                except Exception as e:
                    logger.warning(f"Voice consent update failed: {e}")
        except Exception as e:
            logger.warning(f"Voice state update failed: {e}")
            # M1: surface the failure instead of silently reporting success;
            # the client must be able to distinguish a failed update from an
            # accepted one.
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        return None, None, None

    async def handle_voice_connect(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice connect opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        channel_id = data.get("channel_id")
        if not channel_id:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel_id type: {type(channel_id)}")
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            from src.core import voice
            from src.core.voice import signaling

            voice_state = voice.join_channel(connection.user_id, channel_id)
            signaling.create_voice_connection(connection.user_id, channel_id)

            await self._dispatch_voice_state_update(
                user_id=connection.user_id,
                channel_id_for_recipients=channel_id,
                event_channel_id=channel_id,
                voice_state=voice_state,
            )
        except Exception as e:
            logger.warning(f"Voice connect failed: {e}")
            # M1: report the failure via a close code so the client never
            # mistakes a failed join for a success (the previous bare
            # ``(None, None, None)`` swallow left a stale DB voice_state
            # claiming the user had joined).
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        return None, None, None

    async def handle_voice_disconnect(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice disconnect opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            from src.core import voice
            from src.core.voice import signaling

            # SECURITY (Agent 34 H2): the VOICE_DISCONNECT opcode previously
            # only removed the DB voice_states row and NEVER called the
            # signaling layer, leaking SFU rooms/peers/transports and minted
            # media tokens for the lifetime of the gateway socket. Tear down
            # the signaling connection (SFU leave + local state + token
            # revoke) as well; leave_channel below cleans voice_states.
            try:
                await signaling.disconnect_voice_async(connection.user_id)
            except Exception as e:
                logger.warning(
                    f"Signaling teardown failed for user {connection.user_id}: {e}"
                )

            voice_state = voice.leave_channel(connection.user_id)
            channel_id = getattr(voice_state, "channel_id", None)

            if channel_id:
                await self._dispatch_voice_state_update(
                    user_id=connection.user_id,
                    channel_id_for_recipients=channel_id,
                    event_channel_id=None,
                    voice_state=voice_state,
                )
        except Exception as e:
            logger.warning(f"Voice disconnect failed: {e}")
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        return None, None, None

    async def handle_voice_sdp_offer(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice SDP offer opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            from src.core.voice import signaling

            offer = data.get("sdp")
            raw_channel_id = data.get("channel_id")
            if not offer:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if raw_channel_id is None or raw_channel_id == "":
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            # The channel id must be a positive integer; a malformed value
            # must be rejected here rather than surfacing as an opaque
            # signaling failure.
            try:
                channel_id = int(raw_channel_id)
            except (TypeError, ValueError):
                return None, None, int(GatewayCloseCode.DECODE_ERROR)
            if channel_id <= 0:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            # SECURITY/correctness: the sync wrapper raises RuntimeError
            # whenever an event loop is already running (always the case in
            # this async handler), which silently dropped the answer frame.
            answer = await signaling.handle_sdp_offer_async(
                connection.user_id, channel_id, offer
            )

            # Respond with the dedicated VOICE_SDP_ANSWER opcode (23) and
            # the payload shape the client expects ({from_user_id, sdp}).
            # The answer is an SDPMessage dataclass holding an SDPType enum,
            # which plain json.dumps cannot serialize — build the payload
            # explicitly with the SDP as a plain string.
            # Snowflake ids exceed 2^53 — serialize as strings so JS clients
            # JSON.parse them losslessly (matches VOICE_SPEAKING and the
            # DISPATCH backstop; this reply bypasses _build_dispatch_payload
            # via gateway.py send_json).
            return (
                int(GatewayOpcode.VOICE_SDP_ANSWER),
                {
                    "from_user_id": str(connection.user_id),
                    "sdp": answer.sdp,
                    "type": getattr(answer.sdp_type, "value", "answer"),
                },
                None,
            )
        except Exception as e:
            logger.warning(f"Voice SDP offer failed: {e}")
            # M1: negative ack - a failed offer must never look like success.
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        return None, None, None

    async def handle_voice_sdp_answer(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice SDP answer opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            answer = data.get("sdp")
            channel_id = data.get("channel_id")
            if not answer:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            # SECURITY-correctness (audit: recorded but never delivered): the
            # client has always sent ``channel_id`` here and the server never
            # read it, so the answer's session was bound to nothing. Read and
            # validate it, then require it to match the channel the caller's
            # signaling connection is bound to (the same binding the ICE and
            # quality paths enforce).
            if channel_id is None or channel_id == "":
                return None, None, int(GatewayCloseCode.DECODE_ERROR)
            try:
                channel_id = int(channel_id)
            except (TypeError, ValueError):
                return None, None, int(GatewayCloseCode.DECODE_ERROR)
            if channel_id <= 0:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            # SECURITY-correctness (audit: SFU/media path dead): apply the
            # client's answer to its SFU peer connection so the aiortc DTLS
            # handshake can actually complete and media can flow.
            from src.core.voice import signaling as signaling_module

            mgr = getattr(signaling_module, "_manager", None)
            if mgr is None:
                # Signaling is not configured in this deployment: there is no
                # media plane to apply an answer to.
                return None, None, None

            conn = mgr._connections.get(connection.user_id)
            if conn is None:
                # M1: an answer that matches no session must not look applied.
                logger.warning(
                    f"SDP answer with no signaling connection for user "
                    f"{connection.user_id}"
                )
                return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

            bound_channel = getattr(conn, "channel_id", None)
            if bound_channel is not None and int(bound_channel) != channel_id:
                logger.warning(
                    f"Channel mismatch on SDP answer for user "
                    f"{connection.user_id}: bound={bound_channel} offered={channel_id}"
                )
                return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

            room_id = getattr(conn, "sfu_room_id", None)
            peer_id = getattr(conn, "sfu_peer_id", None)
            if room_id and peer_id:
                sfu = mgr._get_sfu()
                await sfu.apply_remote_answer(room_id, peer_id, answer)
        except Exception as e:
            logger.warning(f"Voice SDP answer failed: {e}")
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        return None, None, None

    async def handle_voice_ice_candidate(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice ICE candidate opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            from src.core.voice import signaling

            candidate = data.get("candidate")
            sdp_mid = data.get("sdp_mid")
            sdp_mline_index = data.get("sdp_mline_index")
            channel_id = data.get("channel_id")

            if not candidate:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if not channel_id:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            try:
                channel_id = int(channel_id)
            except (TypeError, ValueError):
                return None, None, int(GatewayCloseCode.DECODE_ERROR)
            if channel_id <= 0:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            # SECURITY-correctness (audit: trickle ICE never reached the media
            # plane): the async variant both records the candidate AND hands
            # it to the SFU peer connection, so a NAT'd client's reflexive /
            # relay candidate actually reaches the media plane.
            await signaling.handle_ice_candidate_async(
                connection.user_id, channel_id, candidate, sdp_mid, sdp_mline_index
            )
        except Exception as e:
            logger.warning(f"Voice ICE candidate failed: {e}")
            # M1: negative ack on failure.
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        return None, None, None

    async def handle_voice_speaking(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice speaking opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            from src.core import voice

            channel_id = data.get("channel_id")
            if not channel_id:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            # D8: the authoritative voice_state.channel_id is an int while the
            # client-supplied value arrives as a string; a raw comparison
            # always fails and speaking updates would never dispatch. Coerce
            # before comparing.
            try:
                channel_id = int(channel_id)
            except (TypeError, ValueError):
                return None, None, int(GatewayCloseCode.DECODE_ERROR)
            if channel_id <= 0:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            # SECURITY-correctness (audit: callbacks recorded but never
            # delivered): the client has always sent ``speaking``/``volume``
            # and the server read neither, so the peer speaking indicator could
            # never fire. Both are validated and clamped here -- the frame is
            # forwarded to other members, so unvalidated floats would be
            # rendered by their clients.
            speaking = bool(data.get("speaking", False))
            volume = _clamp_volume(data.get("volume", 0))

            state = voice.get_voice_state(connection.user_id)
            if state and state.channel_id == channel_id:
                # Speaking indicators are keepalive traffic for the media
                # plane: refresh the signaling idle clock so the stale
                # sweeper does not disconnect quiet-but-active calls.
                try:
                    from src.core.voice import signaling

                    signaling.record_activity(connection.user_id)
                except Exception:
                    pass
                await self._dispatch_voice_state_update(
                    user_id=connection.user_id,
                    channel_id_for_recipients=channel_id,
                    event_channel_id=channel_id,
                    voice_state=state,
                )
                await self._dispatch_voice_speaking(
                    user_id=connection.user_id,
                    channel_id=channel_id,
                    speaking=speaking,
                    volume=volume,
                )
        except Exception as e:
            logger.warning(f"Voice speaking failed: {e}")
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        return None, None, None

    async def handle_voice_quality(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice quality opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            from src.core.voice import signaling

            quality = data.get("quality")
            channel_id = data.get("channel_id")
            if not channel_id:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            # Forward the client-reported quality to the signaling manager.
            # ``update_quality_hint`` is keyed by (channel_id, peer_id) and
            # records the metrics into the shared quality_data store used by
            # the BandwidthEstimator and get_connection_quality().
            signaling.update_quality_hint(
                channel_id, connection.user_id, quality_level=quality
            )
        except Exception as e:
            logger.warning(f"Voice quality failed: {e}")
            return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)

        return None, None, None

    async def _dispatch_voice_speaking(
        self,
        user_id: int,
        channel_id: int,
        speaking: bool,
        volume: float,
    ) -> None:
        """Relay a speaking indicator to the channel's other members.

        Recipients are resolved through the servers module (same visibility
        filter as the voice-state fan-out), so a non-member never receives the
        frame. The sender is excluded: its own indicator is driven locally by
        its audio analysis.
        """
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup

            if not ws_is_setup():
                return

            server_id = None
            if self._servers:
                try:
                    channel = await run_in_threadpool(
                        self._servers.get_channel, channel_id, user_id
                    )
                    if channel:
                        server_id = getattr(channel, "server_id", None)
                except Exception:
                    pass

            if not server_id:
                return

            target_user_ids = await run_in_threadpool(
                self._get_typing_recipient_ids,
                user_id,
                channel_id,
                server_id,
                False,
            )
            if not target_user_ids:
                return

            await get_dispatcher().dispatch_raw_to_users(
                target_user_ids,
                GatewayOpcode.VOICE_SPEAKING,
                {
                    "user_id": str(user_id),
                    "channel_id": str(channel_id),
                    "speaking": speaking,
                    "volume": volume,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to dispatch voice speaking: {e}")

    async def _dispatch_voice_state_update(
        self,
        user_id: int,
        channel_id_for_recipients: int,
        event_channel_id: Optional[int],
        voice_state: Any,
    ) -> None:
        """Dispatch voice state update to channel members."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events import payloads

            if not ws_is_setup():
                return

            dispatcher = get_dispatcher()

            server_id = None
            if self._servers:
                try:
                    channel = await run_in_threadpool(
                        self._servers.get_channel, channel_id_for_recipients, user_id
                    )
                    if channel:
                        server_id = getattr(channel, "server_id", None)
                except Exception:
                    pass

            event = payloads.create_voice_state_update(
                user_id=user_id,
                channel_id=event_channel_id,
                server_id=server_id,
                self_mute=voice_state.self_mute,
                self_deaf=voice_state.self_deaf,
                mute=getattr(voice_state, "server_mute", False),
                deaf=getattr(voice_state, "server_deaf", False),
                session_id=None,
            )

            target_user_ids: List[int] = []
            if server_id:
                target_user_ids = await run_in_threadpool(
                    self._get_typing_recipient_ids,
                    user_id,
                    channel_id_for_recipients,
                    server_id,
                    True,
                )

            if target_user_ids:
                await dispatcher.dispatch_event(event, target_user_ids)
        except Exception as e:
            logger.warning(f"Failed to dispatch voice state update: {e}")

    def _get_typing_recipient_ids(
        self, user_id: int, channel_id: int, server_id: int, include_self: bool = False
    ) -> List[int]:
        """Return only members who can still view a channel."""
        if not self._servers:
            return []

        member_user_ids = self._servers.get_member_user_ids(
            server_id, exclude_user_id=None if include_self else user_id
        )

        visible_user_ids: List[int] = []
        for member_user_id in member_user_ids:
            try:
                if self._servers.get_channel(channel_id, member_user_id):
                    visible_user_ids.append(member_user_id)
            except Exception:
                continue

        return visible_user_ids
