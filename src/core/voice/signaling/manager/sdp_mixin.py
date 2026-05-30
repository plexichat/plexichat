"""SDP handling mixin for SignalingManager."""

import asyncio
import secrets
from typing import Any, Dict, Optional

import utils.logger as logger

from ..exceptions import NotConnectedError, SDPError
from ..models import SDPMessage, SDPType, SignalingState
from ..sdp import parse_sdp, validate_sdp
from ..sfu.base import TransportDirection


class SDPMixin:
    """Mixin handling SDP offer/answer methods."""

    _voice: Optional[Any]
    _connections: Dict[int, Any]
    _sdp_manipulator: Any
    _ice_builder: Any
    _get_sfu: Any

    def _get_timestamp(self) -> int: ...

    def _get_room_id(self, channel_id: int) -> str: ...

    def create_voice_connection(self, user_id: int, channel_id: int) -> Any: ...

    def handle_sdp_offer(
        self,
        user_id: int,
        channel_id: int,
        sdp: str,
        sdp_type: str = "offer",
    ) -> SDPMessage:
        """
        Handle an SDP offer from a client (sync wrapper).

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            sdp: SDP string
            sdp_type: SDP type (offer/answer)

        Returns:
            SDPMessage with answer
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(
                    self.handle_sdp_offer_async(user_id, channel_id, sdp, sdp_type)
                )
                raise RuntimeError("Use handle_sdp_offer_async in async context")
            else:
                return loop.run_until_complete(
                    self.handle_sdp_offer_async(user_id, channel_id, sdp, sdp_type)
                )
        except RuntimeError as e:
            logger.debug(f"SDP offer async handle failed, using sync fallback: {e}")

        connection = self._connections.get(user_id)
        if not connection:
            self.create_voice_connection(user_id, channel_id)
            connection = self._connections[user_id]

        try:
            parsed_type = SDPType(sdp_type)
            validate_sdp(sdp, parsed_type)
        except Exception as e:
            raise SDPError(f"Invalid SDP: {e}")

        connection.remote_sdp = sdp
        connection.last_activity = self._get_timestamp()

        bitrate = 64000
        if self._voice:
            channel = self._voice.get_voice_channel(channel_id, user_id)
            if channel:
                bitrate = channel.bitrate

        modified_sdp = self._sdp_manipulator.set_bitrate(sdp, bitrate)
        answer_sdp = self._generate_answer_sdp(modified_sdp, connection.session_id)
        connection.local_sdp = answer_sdp
        connection.state = SignalingState.CONNECTING

        logger.debug(f"Processed SDP offer from user {user_id} (sync fallback)")

        return SDPMessage(
            sdp_type=SDPType.ANSWER,
            sdp=answer_sdp,
            session_id=connection.session_id,
        )

    async def handle_sdp_offer_async(
        self,
        user_id: int,
        channel_id: int,
        sdp: str,
        sdp_type: str = "offer",
    ) -> SDPMessage:
        """
        Handle an SDP offer from a client (async version that uses SFU).

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            sdp: SDP string
            sdp_type: SDP type (offer/answer)

        Returns:
            SDPMessage with answer
        """
        connection = self._connections.get(user_id)
        if not connection:
            self.create_voice_connection(user_id, channel_id)
            connection = self._connections[user_id]

        try:
            parsed_type = SDPType(sdp_type)
            validate_sdp(sdp, parsed_type)
        except Exception as e:
            raise SDPError(f"Invalid SDP: {e}")

        connection.remote_sdp = sdp
        connection.last_activity = self._get_timestamp()

        bitrate = 64000
        if self._voice:
            channel = self._voice.get_voice_channel(channel_id, user_id)
            if not channel:
                logger.warning(
                    f"Unauthorized SDP offer for channel {channel_id} by user {user_id}"
                )
                raise NotConnectedError(f"Access denied to channel {channel_id}")
            bitrate = channel.bitrate

        modified_sdp = self._sdp_manipulator.set_bitrate(sdp, bitrate)

        try:
            answer_sdp = await self._get_sfu_answer(
                user_id, channel_id, modified_sdp, connection.session_id
            )
            logger.info(f"Got SDP answer from SFU for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to get SFU answer, using fallback: {e}")
            answer_sdp = self._generate_answer_sdp(modified_sdp, connection.session_id)

        connection.local_sdp = answer_sdp
        connection.state = SignalingState.CONNECTING

        logger.debug(f"Processed SDP offer from user {user_id}")

        return SDPMessage(
            sdp_type=SDPType.ANSWER,
            sdp=answer_sdp,
            session_id=connection.session_id,
        )

    async def _get_sfu_answer(
        self,
        user_id: int,
        channel_id: int,
        offer_sdp: str,
        session_id: str,
    ) -> str:
        """
        Get SDP answer from the SFU by creating a transport.

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            offer_sdp: Client's SDP offer
            session_id: Session ID

        Returns:
            SDP answer string
        """
        sfu = self._get_sfu()
        room_id = self._get_room_id(channel_id)
        peer_id = f"user_{user_id}"

        logger.debug(f"Joining SFU room {room_id} as peer {peer_id}")
        room_info = await sfu.join_room(room_id, peer_id)
        router_caps = room_info.get("routerRtpCapabilities", {})

        logger.debug(f"Creating send transport for peer {peer_id}")
        transport = await sfu.create_transport(
            room_id, peer_id, TransportDirection.SEND
        )

        connection = self._connections.get(user_id)
        if connection:
            connection.transport_id = transport.id
            connection.sfu_room_id = room_id
            connection.sfu_peer_id = peer_id

        logger.debug(f"Completing join for peer {peer_id}")
        await sfu.complete_join(
            room_id,
            peer_id,
            rtp_capabilities=router_caps,
            display_name=f"User {user_id}",
        )

        answer_sdp = self._build_sdp_from_transport(offer_sdp, transport, session_id)

        return answer_sdp

    def _build_sdp_from_transport(
        self, offer_sdp: str, transport, session_id: str
    ) -> str:
        """
        Build an SDP answer from mediasoup transport parameters.

        Args:
            offer_sdp: Client's SDP offer
            transport: SFUTransport with ICE/DTLS parameters
            session_id: Session ID

        Returns:
            SDP answer string
        """
        parsed = parse_sdp(offer_sdp)

        numeric_session_id = str(int(session_id[:16], 16) % (10**18))

        ice_params = transport.ice_parameters
        ice_ufrag = ice_params.get("usernameFragment", secrets.token_hex(4))
        ice_pwd = ice_params.get("password", secrets.token_hex(12))

        dtls_params = transport.dtls_parameters
        fingerprints = dtls_params.get("fingerprints", [])
        fingerprint = (
            fingerprints[0]
            if fingerprints
            else {
                "algorithm": "sha-256",
                "value": ":".join([secrets.token_hex(1).upper() for _ in range(32)]),
            }
        )
        dtls_role = dtls_params.get("role", "auto")

        setup_map = {"auto": "active", "server": "passive", "client": "active"}
        setup = setup_map.get(dtls_role, "active")

        lines = [
            "v=0",
            f"o=- {numeric_session_id} 2 IN IP4 127.0.0.1",
            "s=-",
            "t=0 0",
            "a=group:BUNDLE 0",
            "a=msid-semantic: WMS",
        ]

        for idx, media in enumerate(parsed.get("media", [])):
            media_type = media.get("type", "audio")
            port = media.get("port", 9)
            protocol = media.get("protocol", "UDP/TLS/RTP/SAVPF")
            formats = media.get("formats", ["111"])

            lines.append(f"m={media_type} {port} {protocol} {' '.join(formats)}")
            lines.append("c=IN IP4 0.0.0.0")
            lines.append("a=rtcp:9 IN IP4 0.0.0.0")

            lines.append(f"a=ice-ufrag:{ice_ufrag}")
            lines.append(f"a=ice-pwd:{ice_pwd}")
            lines.append("a=ice-options:trickle")

            for candidate in transport.ice_candidates:
                cand_str = self._format_ice_candidate(candidate)
                if cand_str:
                    lines.append(f"a={cand_str}")

            lines.append(
                f"a=fingerprint:{fingerprint.get('algorithm', 'sha-256')} {fingerprint.get('value', '')}"
            )
            lines.append(f"a=setup:{setup}")
            lines.append(f"a=mid:{idx}")

            lines.append("a=sendrecv")
            lines.append("a=rtcp-mux")
            lines.append("a=rtcp-rsize")

            media_attrs = media.get("attributes", {})
            for fmt in formats:
                rtpmap = media_attrs.get(f"rtpmap:{fmt}") or media_attrs.get("rtpmap")
                if rtpmap:
                    if isinstance(rtpmap, list):
                        for r in rtpmap:
                            if r.startswith(fmt):
                                lines.append(f"a=rtpmap:{r}")
                    else:
                        lines.append(f"a=rtpmap:{fmt} {rtpmap}")
                elif fmt == "111" and media_type == "audio":
                    lines.append("a=rtpmap:111 opus/48000/2")
                    lines.append("a=fmtp:111 minptime=10;useinbandfec=1")

        return "\r\n".join(lines) + "\r\n"

    def _format_ice_candidate(self, candidate: dict) -> str:
        """Format an ICE candidate dict as an SDP candidate line."""
        try:
            foundation = candidate.get("foundation", "1")
            component = candidate.get("component", 1)
            protocol = candidate.get("protocol", "udp").lower()
            priority = candidate.get("priority", 2130706431)
            ip = candidate.get("ip", candidate.get("address", ""))
            port = candidate.get("port", 0)
            typ = candidate.get("type", "host")

            if not ip or not port:
                return ""

            cand = f"candidate:{foundation} {component} {protocol} {priority} {ip} {port} typ {typ}"

            if typ in ("srflx", "relay", "prflx"):
                raddr = candidate.get("relatedAddress", candidate.get("raddr", ""))
                rport = candidate.get("relatedPort", candidate.get("rport", 0))
                if raddr and rport:
                    cand += f" raddr {raddr} rport {rport}"

            if protocol == "tcp":
                tcptype = candidate.get("tcpType", "passive")
                cand += f" tcptype {tcptype}"

            return cand
        except Exception as e:
            logger.warning(f"Failed to format ICE candidate: {e}")
            return ""

    def _generate_answer_sdp(self, offer_sdp: str, session_id: str) -> str:
        """
        Generate an SDP answer from an offer.

        In production, this would be generated by the SFU.
        This is a simplified implementation for signaling flow.
        """
        parsed = parse_sdp(offer_sdp)

        numeric_session_id = str(int(session_id[:16], 16) % (10**18))

        ice_ufrag = secrets.token_hex(4)
        ice_pwd = secrets.token_hex(12)
        fingerprint = ":".join([secrets.token_hex(1).upper() for _ in range(32)])

        lines = [
            "v=0",
            f"o=- {numeric_session_id} 2 IN IP4 127.0.0.1",
            "s=-",
            "t=0 0",
            "a=group:BUNDLE 0",
            "a=msid-semantic: WMS",
        ]

        for idx, media in enumerate(parsed.get("media", [])):
            media_type = media.get("type", "audio")
            port = media.get("port", 9)
            protocol = media.get("protocol", "UDP/TLS/RTP/SAVPF")
            formats = media.get("formats", ["111"])

            lines.append(f"m={media_type} {port} {protocol} {' '.join(formats)}")
            lines.append("c=IN IP4 0.0.0.0")
            lines.append("a=rtcp:9 IN IP4 0.0.0.0")

            lines.append(f"a=ice-ufrag:{ice_ufrag}")
            lines.append(f"a=ice-pwd:{ice_pwd}")
            lines.append("a=ice-options:trickle")
            lines.append(f"a=fingerprint:sha-256 {fingerprint}")
            lines.append("a=setup:active")
            lines.append(f"a=mid:{idx}")

            lines.append("a=sendrecv")

            lines.append("a=rtcp-mux")

            media_attrs = media.get("attributes", {})
            for fmt in formats:
                rtpmap = media_attrs.get(f"rtpmap:{fmt}") or media_attrs.get("rtpmap")
                if rtpmap:
                    if isinstance(rtpmap, list):
                        for r in rtpmap:
                            if r.startswith(fmt):
                                lines.append(f"a=rtpmap:{r}")
                    else:
                        lines.append(f"a=rtpmap:{fmt} {rtpmap}")
                elif fmt == "111" and media_type == "audio":
                    lines.append("a=rtpmap:111 opus/48000/2")
                    lines.append("a=fmtp:111 minptime=10;useinbandfec=1")

        return "\r\n".join(lines) + "\r\n"
