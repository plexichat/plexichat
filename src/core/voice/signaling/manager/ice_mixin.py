"""ICE handling mixin for SignalingManager."""

from typing import Dict, Optional, Any

import utils.logger as logger

from ..exceptions import NotConnectedError
from ..ice import parse_ice_candidate
from ..models import SignalingState


class ICEMixin:
    """Mixin handling ICE candidate methods."""

    _connections: Dict[int, Any]
    _ice_manager: Any

    def _get_timestamp(self) -> int: ...

    def handle_ice_candidate(
        self,
        user_id: int,
        channel_id: int,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> bool:
        """
        Handle an ICE candidate from a client.

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            candidate: ICE candidate string
            sdp_mid: Media stream ID
            sdp_mline_index: Media line index

        Returns:
            True if processed successfully
        """
        connection = self._connections.get(user_id)
        if not connection:
            raise NotConnectedError(
                "User not connected to voice", user_id=user_id, channel_id=channel_id
            )

        ice_candidate = parse_ice_candidate(candidate, sdp_mid, sdp_mline_index)

        self._ice_manager.add_candidate(
            connection.session_id, candidate, sdp_mid, sdp_mline_index
        )
        connection.ice_candidates.append(ice_candidate)
        connection.last_activity = self._get_timestamp()

        if (
            len(connection.ice_candidates) >= 1
            and connection.state == SignalingState.CONNECTING
        ):
            connection.state = SignalingState.CONNECTED
            logger.debug(f"User {user_id} voice connection established")

        return True
