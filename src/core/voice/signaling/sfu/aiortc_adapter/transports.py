"""WebRTC transports and DTLS parameters for the aiortc SFU adapter."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict

import utils.logger as logger

from ...exceptions import SFUConnectionError
from ..base import SFUTransport, TransportDirection
from .constants import MAX_TRANSPORTS_PER_PEER
from .errors import SFUValidationError
from .models import AiortcPeer
from .sdp import (
    _parse_dtls_params_from_sdp,
    _parse_ice_candidates_from_sdp,
    _parse_ice_params_from_sdp,
)
from .validation import validate_dtls_parameters, validate_identifier, validate_room_id


class TransportsMixin:
    """Transport creation and DTLS parameter hand-off."""

    _rooms: Dict[str, Dict[str, AiortcPeer]]

    async def create_transport(
        self,
        room_id: str,
        peer_id: str,
        direction: TransportDirection,
    ) -> SFUTransport:
        """Create a WebRTC transport for a peer with real ICE/DTLS parameters."""
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        if not isinstance(direction, TransportDirection):
            raise SFUValidationError("Invalid transport direction")
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError("Peer not in room")

        peer = self._rooms[room_id][peer_id]
        if len(peer.transports) >= MAX_TRANSPORTS_PER_PEER:
            raise SFUValidationError("Transport limit exceeded")
        transport_id = str(uuid.uuid4())
        pc = peer.pc

        if pc.localDescription is None:
            # The offer dance is only legal from "stable". In a pending remote
            # offer (the client-offer flow) this peer is the answerer and
            # ``apply_remote_offer`` must have already installed the answer --
            # raise a diagnosable error instead of letting aiortc surface an
            # InvalidStateError from deep inside setLocalDescription.
            if pc.signalingState != "stable":
                raise SFUConnectionError(
                    f"Cannot create a local offer for peer {peer_id} in "
                    f"signaling state '{pc.signalingState}'"
                )
            pc.createDataChannel("transport-init-" + transport_id)
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            if pc.iceGatheringState != "complete":
                done = asyncio.Event()

                @pc.on("icegatheringstatechange")
                def on_ice_state():
                    if pc.iceGatheringState == "complete":
                        done.set()

                try:
                    await asyncio.wait_for(done.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.debug(f"ICE gathering timed out for peer {peer_id}")

        if pc.localDescription is None:
            raise SFUConnectionError("Failed to get local description")
        sdp = pc.localDescription.sdp

        transport = SFUTransport(
            id=transport_id,
            direction=direction,
            ice_parameters=_parse_ice_params_from_sdp(sdp),
            ice_candidates=_parse_ice_candidates_from_sdp(sdp),
            dtls_parameters=_parse_dtls_params_from_sdp(sdp),
        )

        peer.transports[transport_id] = transport
        logger.debug(f"Created transport {transport_id} for peer {peer_id}")
        return transport

    async def connect_transport(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        dtls_parameters: Dict[str, Any],
    ) -> bool:
        """Connect a transport with remote DTLS parameters."""
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            transport_id = validate_identifier(transport_id, "transport_id")
            dtls = validate_dtls_parameters(dtls_parameters)
        except SFUValidationError:
            return False
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False
        peer = self._rooms[room_id][peer_id]
        if transport_id not in peer.transports:
            return False
        peer.transports[transport_id].dtls_parameters = dtls
        logger.debug("Connected transport")
        return True
