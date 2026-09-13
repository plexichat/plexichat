"""ICE handling mixin for SignalingManager."""

from typing import TYPE_CHECKING, Dict, Optional, Any

import utils.logger as logger

from ..exceptions import NotConnectedError
from ..ice import parse_ice_candidate
from ..models import SignalingState


class ICEMixin:
    """Mixin handling ICE candidate methods."""

    if TYPE_CHECKING:

        def _get_timestamp(self) -> int: ...

    _connections: Dict[int, Any]
    _ice_manager: Any
    _get_sfu: Any

    def _record_ice_candidate(
        self,
        user_id: int,
        channel_id: int,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> Optional[Any]:
        """Validate and record a candidate, returning its connection.

        Returns ``None`` when the connection is bound to a different channel
        (the caller's candidate is refused). Raises when there is no connection
        at all. ``parse_ice_candidate`` raises on malformed input, so reaching
        the state flip means we hold a VALID candidate — that is the signal to
        move CONNECTING -> CONNECTED. (The old ``len(...) >= 1`` check was a
        tautology: the candidate had just been appended above, so the condition
        could never be false; it only looked like a gate.)
        """
        connection = self._connections.get(user_id)
        if not connection:
            raise NotConnectedError(
                "User not connected to voice", user_id=user_id, channel_id=channel_id
            )

        # SECURITY (Agent 34 H4): candidates must belong to the channel the
        # connection is bound to; otherwise clients can cross-inject ICE
        # candidates into arbitrary channels' sessions.
        if connection.channel_id != channel_id:
            logger.warning(
                f"Channel mismatch on ICE candidate for user {user_id}: "
                f"bound={connection.channel_id} offered={channel_id}"
            )
            return None

        ice_candidate = parse_ice_candidate(candidate, sdp_mid, sdp_mline_index)

        self._ice_manager.add_candidate(
            connection.session_id, candidate, sdp_mid, sdp_mline_index
        )
        connection.ice_candidates.append(ice_candidate)
        connection.last_activity = self._get_timestamp()

        if connection.state == SignalingState.CONNECTING:
            connection.state = SignalingState.CONNECTED
            logger.debug(f"User {user_id} voice connection established")

        return connection

    def handle_ice_candidate(
        self,
        user_id: int,
        channel_id: int,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> bool:
        """
        Handle an ICE candidate from a client (sync version).

        Records the candidate. Media adapters that terminate the session
        themselves also need it delivered to their peer connection — use
        :meth:`handle_ice_candidate_async` from an event loop for that.

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            candidate: ICE candidate string
            sdp_mid: Media stream ID
            sdp_mline_index: Media line index

        Returns:
            True if processed successfully
        """
        return (
            self._record_ice_candidate(
                user_id, channel_id, candidate, sdp_mid, sdp_mline_index
            )
            is not None
        )

    async def handle_ice_candidate_async(
        self,
        user_id: int,
        channel_id: int,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> bool:
        """Record a candidate AND deliver it to the peer's media connection.

        SECURITY-correctness (audit: trickle ICE never reached the media
        plane): the candidate was stored on the signaling connection but no
        adapter method existed to apply it, so the SFU never learned the
        client's address and could only connect opportunistically via
        peer-reflexive discovery. A backend that negotiates out of band
        accepts this as a no-op; aiortc feeds it to ``addIceCandidate``.
        """
        connection = self._record_ice_candidate(
            user_id, channel_id, candidate, sdp_mid, sdp_mline_index
        )
        if connection is None:
            return False

        room_id = getattr(connection, "sfu_room_id", None)
        peer_id = getattr(connection, "sfu_peer_id", None)
        if not room_id or not peer_id:
            # The SFU peer is established with the SDP answer; a candidate
            # arriving before that is still recorded for the next negotiation.
            return True

        try:
            sfu = self._get_sfu()
            delivered = await sfu.add_ice_candidate(
                room_id, peer_id, candidate, sdp_mid, sdp_mline_index
            )
        except Exception as e:
            logger.warning(
                f"Failed to deliver ICE candidate to SFU for user {user_id}: {e}"
            )
            return True

        if not delivered:
            logger.debug(
                f"SFU did not accept ICE candidate for user {user_id} "
                f"(room={room_id}, peer={peer_id})"
            )
        return True
