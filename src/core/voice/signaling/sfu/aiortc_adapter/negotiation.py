"""SDP/ICE negotiation for the aiortc SFU adapter."""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Dict, Optional, cast

import utils.logger as logger

from ...exceptions import SFUConnectionError
from .errors import SFUValidationError
from .models import AiortcPeer
from .runtime import _rtc_ice_candidate, _rtc_session_description
from .validation import (
    validate_ice_candidate_payload,
    validate_identifier,
    validate_room_id,
    validate_sdp_payload,
)


class NegotiationMixin:
    """Client offers/answers, trickled candidates and SFU renegotiation."""

    _rooms: Dict[str, Dict[str, AiortcPeer]]
    _renegotiation_sink: Any

    async def apply_remote_offer(
        self,
        room_id: str,
        peer_id: str,
        offer_sdp: str,
    ) -> Dict[str, Any]:
        """Answer the client's offer on the peer's aiortc connection."""
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        offer_sdp = validate_sdp_payload(offer_sdp, "offer SDP")
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            raise SFUConnectionError("Peer not in room")
        try:
            remote = _rtc_session_description(sdp=offer_sdp, type="offer")
            await peer.pc.setRemoteDescription(remote)
        except SFUConnectionError:
            raise
        except Exception as exc:
            raise SFUConnectionError("Failed to apply remote offer") from exc

        if peer.pc.signalingState == "have-remote-offer":
            try:
                answer = await peer.pc.createAnswer()
                await peer.pc.setLocalDescription(answer)
            except Exception as exc:
                raise SFUConnectionError("Failed to build SFU answer") from exc

        local = peer.pc.localDescription
        if local is not None and local.sdp:
            return {"sdp": local.sdp, "type": local.type}
        return {}

    async def apply_remote_answer(
        self,
        room_id: str,
        peer_id: str,
        answer_sdp: str,
    ) -> bool:
        """Apply the client's answer so the aiortc DTLS handshake can run."""
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            answer_sdp = validate_sdp_payload(answer_sdp, "answer SDP")
        except SFUValidationError:
            return False
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            return False
        try:
            remote = _rtc_session_description(sdp=answer_sdp, type="answer")
            await peer.pc.setRemoteDescription(remote)
            return True
        except Exception:
            logger.warning("Failed to apply remote answer")
            return False

    async def add_ice_candidate(
        self,
        room_id: str,
        peer_id: str,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> bool:
        """Feed a trickled client candidate into the peer connection."""
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            cand, mid, idx = validate_ice_candidate_payload(
                candidate, sdp_mid, sdp_mline_index
            )
        except SFUValidationError:
            return False
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            return False
        try:
            ice_mod = cast(Any, importlib.import_module("...ice", __package__))
            parsed = ice_mod.ICECandidateParser().parse(cand)
            remote = _rtc_ice_candidate(
                component=int(parsed["component"]),
                foundation=str(parsed["foundation"]),
                ip=str(parsed["address"]),
                port=int(parsed["port"]),
                priority=int(parsed["priority"]),
                protocol=str(parsed["transport"]).lower(),
                type=str(parsed["type"]),
                relatedAddress=parsed.get("related_address"),
                relatedPort=parsed.get("related_port"),
                sdpMid=mid,
                sdpMLineIndex=idx,
                tcpType=parsed.get("tcp_type"),
            )
            await peer.pc.addIceCandidate(remote)
            return True
        except Exception:
            logger.debug("Failed to add ICE candidate")
            return False

    async def _request_renegotiation(
        self, room_id: str, peer_id: str, peer: AiortcPeer
    ) -> bool:
        """Offer to a peer that has already negotiated, and deliver it.

        aiortc never emits ``negotiationneeded``, so the SFU can never start an
        offer by itself: a track forwarded to a peer that has already finished
        its handshake would be added to a transceiver the client never learns
        about. The offer is built here and handed to the renegotiation sink;
        without a sink there is no signaling path, so this reports ``False``
        rather than pretending the track was delivered.
        """
        if peer.pc is None or peer.pc.remoteDescription is None:
            # Nothing to renegotiate with yet: the peer's first offer is still
            # to come, and the track will be bound when it arrives.
            return False
        try:
            offer = await peer.pc.createOffer()
            await peer.pc.setLocalDescription(offer)
        except Exception as exc:
            logger.debug(f"Renegotiation offer failed for peer {peer_id}: {exc}")
            return False
        local = peer.pc.localDescription
        if local is None or not local.sdp:
            return False

        sink = self._renegotiation_sink
        if sink is None:
            logger.debug(
                f"Offer for peer {peer_id} in room {room_id} has no "
                "renegotiation sink and no signaling path to the client"
            )
            return False
        try:
            result = sink(room_id, peer_id, local.sdp)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning(
                f"Failed to deliver renegotiation offer for {peer_id}: {exc}"
            )
            return False
        return True
