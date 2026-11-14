"""Room and peer lifecycle for the aiortc SFU adapter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import utils.logger as logger

from ...exceptions import SFUConnectionError
from ..base import RoomInfo
from .constants import (
    DEFAULT_MAX_INCOMING_TRACKS,
    MAX_PEERS_PER_ROOM,
    MAX_TRACKS_PER_KIND,
)
from .errors import SFUValidationError
from .models import AiortcPeer
from .recording import record_audio_track
from .runtime import _audio_processing_track_cls, _make_rtc_peer_connection
from .validation import validate_identifier, validate_room_id


class RoomsMixin:
    """Rooms, the peers in them, and the tracks each peer brings."""

    _rooms: Dict[str, Dict[str, AiortcPeer]]
    _recordings: Dict[str, Dict[str, Any]]
    _acoustic_defense: Any
    _last_quality_sample: Dict[str, Any]

    # Implemented by the mixins this one is composed after.
    def _sender_view(self, track: Any) -> Any: ...
    def _close_fanout(self, track: Any) -> None: ...
    def _remove_forwarded_senders(self, room_id: str, track: Any) -> None: ...
    @staticmethod
    def _prune_incoming_tracks(
        tracks: List[Any], max_len: int = DEFAULT_MAX_INCOMING_TRACKS
    ) -> None: ...
    async def stop_recording(self, room_id: str) -> Optional[List[str]]: ...

    async def create_room(self, room_id: str) -> RoomInfo:
        """Create a new room."""
        room_id = validate_room_id(room_id)
        if room_id in self._rooms:
            logger.warning("SFU room already exists; returning existing room")
            room_info = await self.get_room_info(room_id)
            if room_info is None:
                raise SFUConnectionError("Failed to get room info")
            return room_info

        self._rooms[room_id] = {}
        logger.info("Created aiortc SFU room")
        return RoomInfo(id=room_id, peers=[], producers=[])

    async def close_room(self, room_id: str) -> bool:
        """Close a room and clean up all peers."""
        try:
            room_id = validate_room_id(room_id)
        except SFUValidationError:
            return False
        if room_id not in self._rooms:
            return False

        if room_id in self._recordings:
            await self.stop_recording(room_id)

        for peer_id, peer in self._rooms[room_id].items():
            for track in list(peer.producer_tracks.values()):
                self._close_fanout(track)
            try:
                await peer.pc.close()
            except Exception as exc:
                logger.warning(f"Error closing peer connection: {exc}")
            self._last_quality_sample.pop(peer_id, None)

        del self._rooms[room_id]
        logger.info("Closed aiortc SFU room")
        return True

    async def join_room(self, room_id: str, peer_id: str) -> Dict[str, Any]:
        """Join a peer to a room."""
        room_id = validate_room_id(room_id)
        peer_id = validate_identifier(peer_id, "peer_id")
        if room_id not in self._rooms:
            await self.create_room(room_id)

        if peer_id in self._rooms[room_id]:
            logger.warning("Peer already in room")
            return await self.get_router_capabilities(room_id)

        if len(self._rooms[room_id]) >= MAX_PEERS_PER_ROOM:
            raise SFUValidationError("Room peer limit exceeded")
        pc = _make_rtc_peer_connection()
        peer = AiortcPeer(peer_id=peer_id, pc=pc, room_id=room_id)
        self._rooms[room_id][peer_id] = peer

        @pc.on("track")
        async def on_track(track):
            processed_track = track
            if track.kind == "audio":
                if self._acoustic_defense.enabled:
                    # Contain the wrap: a processing failure must not stop the
                    # track from being registered for this peer (the caller
                    # would otherwise fall back to the unwrapped receiver
                    # track and lose the acoustic defense silently).
                    try:
                        processed_track = _audio_processing_track_cls()(
                            track, self._acoustic_defense
                        )
                    except Exception as exc:
                        logger.warning(
                            f"Acoustic defense wrap failed for peer {peer_id} "
                            f"in room {room_id}, using the raw track: {exc}"
                        )
                        processed_track = track
                # L1 (Agent 34): renegotiation redelivers tracks; appending
                # unconditionally accumulated duplicate/ghost entries. Dedupe
                # and prune to a bounded set per kind.
                if (
                    len(peer.incoming_audio_tracks) < MAX_TRACKS_PER_KIND
                    and processed_track not in peer.incoming_audio_tracks
                ):
                    peer.incoming_audio_tracks.append(processed_track)
                    self._prune_incoming_tracks(
                        peer.incoming_audio_tracks, DEFAULT_MAX_INCOMING_TRACKS
                    )
                logger.debug("Audio track received")
            elif track.kind == "video":
                if (
                    len(peer.incoming_video_tracks) < MAX_TRACKS_PER_KIND
                    and track not in peer.incoming_video_tracks
                ):
                    peer.incoming_video_tracks.append(track)
                    self._prune_incoming_tracks(
                        peer.incoming_video_tracks, DEFAULT_MAX_INCOMING_TRACKS
                    )
                logger.debug("Video track received")

            if track.kind == "audio":
                await record_audio_track(
                    self._recordings.get(room_id), room_id, peer_id, processed_track
                )

        @pc.on("negotiationneeded")
        async def on_negotiation_needed():
            try:
                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)
            except Exception as e:
                logger.warning(f"Negotiation failed for peer {peer_id}: {e}")

        # Forward existing producers to the new peer
        for existing_id, existing_peer in self._rooms[room_id].items():
            if existing_id == peer_id:
                continue
            for producer_id, track in existing_peer.producer_tracks.items():
                if track is None:
                    continue
                try:
                    peer.pc.addTrack(self._sender_view(track))
                except Exception as e:
                    logger.debug(
                        f"Could not add track {producer_id} from {existing_id} "
                        f"to new peer {peer_id}: {e}"
                    )

        logger.info("Peer joined room")
        return await self.get_router_capabilities(room_id)

    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """Remove a peer from a room."""
        if room_id not in self._rooms:
            return False

        if peer_id not in self._rooms[room_id]:
            return False

        peer = self._rooms[room_id][peer_id]

        # L1 (Agent 34): before tearing down this peer, remove the tracks it
        # produced from every other peer's connection so ghost senders do not
        # outlive the producing peer.
        produced_tracks = [
            track
            for track in list(peer.producer_tracks.values())
            + list(peer.incoming_audio_tracks)
            + list(peer.incoming_video_tracks)
        ]
        for track in produced_tracks:
            self._remove_forwarded_senders(room_id, track)
        for track in list(peer.producer_tracks.values()):
            self._close_fanout(track)

        try:
            await peer.pc.close()
        except Exception as e:
            logger.warning(f"Error closing peer connection: {e}")

        peer.incoming_audio_tracks.clear()
        peer.incoming_video_tracks.clear()
        peer.producer_tracks.clear()
        peer.producers.clear()
        peer.consumers.clear()
        peer.degraded_senders.clear()

        del self._rooms[room_id][peer_id]
        self._last_quality_sample.pop(peer_id, None)
        logger.info("Peer left room")

        if not self._rooms[room_id]:
            await self.close_room(room_id)

        return True

    async def get_room_info(self, room_id: str) -> Optional[RoomInfo]:
        """Get information about a room."""
        if room_id not in self._rooms:
            return None

        peers = list(self._rooms[room_id].keys())
        producers: List[Any] = []

        for peer in self._rooms[room_id].values():
            producers.extend(peer.producers.keys())

        return RoomInfo(id=room_id, peers=peers, producers=producers)

    async def get_router_capabilities(self, room_id: str) -> Dict[str, Any]:
        """Get RTP capabilities for a room's router."""
        return {
            "codecs": [
                {
                    "kind": "audio",
                    "mimeType": "audio/opus",
                    "clockRate": 48000,
                    "channels": 2,
                },
                {
                    "kind": "video",
                    "mimeType": "video/VP8",
                    "clockRate": 90000,
                },
                {
                    "kind": "video",
                    "mimeType": "video/VP9",
                    "clockRate": 90000,
                },
                {
                    "kind": "video",
                    "mimeType": "video/H264",
                    "clockRate": 90000,
                },
            ],
            "headerExtensions": [],
            "fecMechanisms": [],
        }
