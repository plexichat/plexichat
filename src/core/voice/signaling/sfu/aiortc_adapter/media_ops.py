"""Producers and consumers for the aiortc SFU adapter."""

from __future__ import annotations

import uuid
from typing import Any, Dict

import utils.logger as logger

from ...exceptions import SFUConnectionError
from ..base import MediaKind, SFUConsumer, SFUProducer
from .constants import MAX_CONSUMERS_PER_PEER, MAX_PRODUCERS_PER_PEER
from .errors import SFUValidationError
from .models import AiortcPeer
from .runtime import _audio_processing_track_cls
from .validation import validate_identifier, validate_payload_mapping, validate_room_id


class MediaOpsMixin:
    """Producer/consumer creation, forwarding, pause/resume and close."""

    _rooms: Dict[str, Dict[str, AiortcPeer]]
    _acoustic_defense: Any

    # Implemented by the mixins this one is composed after.
    def _sender_view(self, track: Any) -> Any: ...

    @staticmethod
    def _sender_for_track(peer: AiortcPeer, track: Any) -> Any: ...
    def _close_fanout(self, track: Any) -> None: ...
    def _remove_forwarded_senders(self, room_id: str, track: Any) -> None: ...
    async def _request_renegotiation(
        self, room_id: str, peer_id: str, peer: AiortcPeer
    ) -> bool: ...

    def _find_track_by_kind(self, peer: AiortcPeer, kind: MediaKind) -> Any:
        """Locate a MediaStreamTrack of the requested kind on a peer's PC."""
        if kind == MediaKind.AUDIO and peer.incoming_audio_tracks:
            return peer.incoming_audio_tracks[-1]
        if kind == MediaKind.VIDEO and peer.incoming_video_tracks:
            return peer.incoming_video_tracks[-1]

        for receiver in peer.pc.getReceivers():
            if receiver.track and receiver.track.kind == kind.value:
                return receiver.track

        return None

    async def _forward_track_to_peer(
        self,
        track: Any,
        producer_id: str,
        kind: MediaKind,
        target_peer: AiortcPeer,
    ) -> None:
        """Add a track to a target peer's connection and trigger renegotiation."""
        if track is None:
            return

        if self._sender_for_track(target_peer, track) is not None:
            return

        try:
            target_peer.pc.addTrack(self._sender_view(track))
            await self._request_renegotiation(
                target_peer.room_id, target_peer.peer_id, target_peer
            )
        except Exception as e:
            logger.debug(
                f"Could not forward track {producer_id} to peer "
                f"{target_peer.peer_id}: {e}"
            )

    async def produce(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        kind: MediaKind,
        rtp_parameters: Dict[str, Any],
    ) -> SFUProducer:
        """Create a producer to send media and forward the track to all other peers."""
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        transport_id = validate_identifier(transport_id, "transport_id")
        rtp_parameters = validate_payload_mapping(rtp_parameters, "rtp_parameters")
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError("Peer not in room")

        peer = self._rooms[room_id][peer_id]
        if len(peer.producers) >= MAX_PRODUCERS_PER_PEER:
            raise SFUValidationError("Producer limit exceeded")
        producer_id = str(uuid.uuid4())

        track = self._find_track_by_kind(peer, kind)

        # Wrap audio tracks with acoustic defense processing. The receiver
        # track was already wrapped on arrival (``on_track``), so only wrap a
        # track that is not -- otherwise every frame is processed twice.
        if (
            kind == MediaKind.AUDIO
            and track is not None
            and self._acoustic_defense.enabled
            and not isinstance(track, _audio_processing_track_cls())
        ):
            track = _audio_processing_track_cls()(track, self._acoustic_defense)

        producer = SFUProducer(
            id=producer_id,
            kind=kind,
            rtp_parameters=rtp_parameters,
            paused=False,
        )

        peer.producers[producer_id] = producer
        if track:
            peer.producer_tracks[producer_id] = track

        for other_id, other_peer in self._rooms[room_id].items():
            if other_id == peer_id:
                continue
            await self._forward_track_to_peer(track, producer_id, kind, other_peer)

        logger.info(
            f"Created producer {producer_id} ({kind.value}) for peer {peer_id}"
            f" and forwarded to {len(self._rooms[room_id]) - 1} peer(s)"
        )

        return producer

    async def consume(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: Dict[str, Any],
    ) -> SFUConsumer:
        """Create a consumer to receive media from a producer."""
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        transport_id = validate_identifier(transport_id, "transport_id")
        producer_id = validate_identifier(producer_id, "producer_id")
        rtp_capabilities = validate_payload_mapping(
            rtp_capabilities, "rtp_capabilities"
        )
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            raise SFUConnectionError("Peer not in room")

        peer = self._rooms[room_id][peer_id]
        if len(peer.consumers) >= MAX_CONSUMERS_PER_PEER:
            raise SFUValidationError("Consumer limit exceeded")
        consumer_id = str(uuid.uuid4())

        producer = None
        producer_track = None
        producer_kind = MediaKind.AUDIO
        for p in self._rooms[room_id].values():
            if producer_id in p.producers:
                producer = p.producers[producer_id]
                producer_track = p.producer_tracks.get(producer_id)
                producer_kind = producer.kind
                break

        if not producer:
            raise SFUConnectionError(f"Producer {producer_id} not found")

        if producer_track is not None:
            already_wired = self._sender_for_track(peer, producer_track) is not None
            if not already_wired:
                try:
                    peer.pc.addTrack(self._sender_view(producer_track))
                    await self._request_renegotiation(room_id, peer_id, peer)
                except Exception as e:
                    logger.debug(
                        f"Could not wire consumer {consumer_id} for producer "
                        f"{producer_id} on peer {peer_id}: {e}"
                    )

        consumer = SFUConsumer(
            id=consumer_id,
            producer_id=producer_id,
            kind=producer_kind,
            rtp_parameters=rtp_capabilities,
            paused=False,
        )

        peer.consumers[consumer_id] = consumer
        logger.info(
            f"Created consumer {consumer_id} ({producer_kind.value}) "
            f"for peer {peer_id} consuming producer {producer_id}"
        )

        return consumer

    async def pause_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Pause a producer."""
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            producer_id = validate_identifier(producer_id, "producer_id")
        except SFUValidationError:
            return False
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False
        peer = self._rooms[room_id][peer_id]
        if producer_id not in peer.producers:
            return False

        peer.producers[producer_id].paused = True
        logger.debug(f"Paused producer {producer_id}")
        return True

    async def resume_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Resume a producer."""
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            producer_id = validate_identifier(producer_id, "producer_id")
        except SFUValidationError:
            return False
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False
        peer = self._rooms[room_id][peer_id]
        if producer_id not in peer.producers:
            return False

        peer.producers[producer_id].paused = False
        logger.debug(f"Resumed producer {producer_id}")
        return True

    async def close_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Close a producer."""
        try:
            room_id = validate_room_id(room_id)
            peer_id = validate_identifier(peer_id, "peer_id")
            producer_id = validate_identifier(producer_id, "producer_id")
        except SFUValidationError:
            return False
        if room_id not in self._rooms or peer_id not in self._rooms[room_id]:
            return False
        peer = self._rooms[room_id][peer_id]
        if producer_id not in peer.producers:
            return False

        del peer.producers[producer_id]
        track = peer.producer_tracks.pop(producer_id, None)
        # L1 (Agent 34): closing a producer must also stop forwarding its
        # track to every other peer (ghost senders otherwise persist).
        if track is not None:
            self._remove_forwarded_senders(room_id, track)
            self._close_fanout(track)
        logger.debug(f"Closed producer {producer_id}")
        return True
