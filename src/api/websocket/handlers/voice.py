"""
Voice handlers - Handle voice-related opcodes (voice state, WebRTC signaling).
"""

from typing import Optional, Dict, Any, Tuple, List, TYPE_CHECKING

import utils.logger as logger
from starlette.concurrency import run_in_threadpool

from src.api.websocket.connection import Connection
from src.api.websocket.opcodes import GatewayCloseCode

if TYPE_CHECKING:
    from src.core.servers.manager import ServerManager


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

            await self._dispatch_voice_state_update(
                user_id=connection.user_id,
                channel_id_for_recipients=channel_id,
                event_channel_id=channel_id,
                voice_state=voice_state,
            )
        except Exception as e:
            logger.warning(f"Voice state update failed: {e}")

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
            channel_id = data.get("channel_id")
            if not offer:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if not channel_id:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            answer = signaling.handle_sdp_offer(connection.user_id, channel_id, offer)

            return int(0), {"sdp": answer}, None
        except Exception as e:
            logger.warning(f"Voice SDP offer failed: {e}")

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
            if not answer:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)

            # SDP answers are handled by the SFU, no need to process here
            pass
        except Exception as e:
            logger.warning(f"Voice SDP answer failed: {e}")

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

            signaling.handle_ice_candidate(
                connection.user_id, channel_id, candidate, sdp_mid, sdp_mline_index
            )
        except Exception as e:
            logger.warning(f"Voice ICE candidate failed: {e}")

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
            # set_speaking not implemented in signaling module yet
            pass
        except Exception as e:
            logger.warning(f"Voice speaking failed: {e}")

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

            # Use update_quality_hint instead of set_quality
            signaling.update_quality_hint(
                connection.user_id, channel_id, quality_level=quality
            )
        except Exception as e:
            logger.warning(f"Voice quality failed: {e}")

        return None, None, None

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
