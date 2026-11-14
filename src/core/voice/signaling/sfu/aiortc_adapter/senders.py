"""Sender hygiene: fan-out relays and the senders they feed."""

from __future__ import annotations

from typing import Any, Dict, List

import utils.logger as logger

from ..fanout import FanoutView, TrackFanout
from .constants import DEFAULT_MAX_INCOMING_TRACKS
from .media import _same_sender_track
from .models import AiortcPeer


class SenderMixin:
    """Per-track fan-out, and removal of senders that outlived their track."""

    _fanouts: Dict[Any, TrackFanout]
    _rooms: Dict[str, Dict[str, AiortcPeer]]

    @staticmethod
    def _prune_incoming_tracks(
        tracks: List[Any], max_len: int = DEFAULT_MAX_INCOMING_TRACKS
    ) -> None:
        """Bound an incoming-track list so renegotiation cannot grow it."""
        if len(tracks) > max_len:
            del tracks[: len(tracks) - max_len]

    def _fanout_for(self, track: Any) -> TrackFanout:
        """The shared read-once relay for ``track``, created on first use."""
        relay = self._fanouts.get(track)
        if relay is None or relay.closed:
            relay = TrackFanout(track)
            self._fanouts[track] = relay
        return relay

    def _sender_view(self, track: Any) -> FanoutView:
        """A per-sender view of ``track`` that receives every frame.

        Handing the same track to several senders made each ``recv()`` *take* a
        frame from a shared queue, so peers split the stream; and aiortc stops a
        sender's track when its RTP task exits, so one peer leaving ended the
        room's media. A view reads from the relay's private queue and its
        ``stop()`` detaches that sender alone.
        """
        return self._fanout_for(track).subscribe()

    def _close_fanout(self, track: Any) -> None:
        """Drop ``track``'s relay once nothing forwards it any more.

        The relay stops itself through :meth:`TrackFanout.close`; the adapter
        used to cancel the relay's private ``_task`` from here, which coupled it
        to internals the relay is free to change.
        """
        relay = self._fanouts.pop(track, None)
        if relay is not None:
            relay.close()

    @staticmethod
    def _sender_for_track(peer: AiortcPeer, track: Any) -> Any:
        """Return a sender already forwarding ``track``, including suspended ones.

        A sender suspended by a degradation hint reports ``track is None``, so
        identity against ``sender.track`` alone is not enough: re-adding the
        track would create a duplicate sender and trigger a spurious
        renegotiation.
        """
        if track is None:
            return None
        for sender in peer.pc.getSenders():
            if _same_sender_track(getattr(sender, "track", None), track):
                return sender
            if peer.degraded_senders.get(sender) is track:
                return sender
        return None

    def _remove_forwarded_senders(self, room_id: str, track: Any) -> None:
        """Remove every sender forwarding ``track`` on other peers' PCs.

        L1 (Agent 34): forwarded tracks added to other peers were never
        removed when the producer closed or the producing peer left, leaving
        ghost senders on live peer connections.
        """
        if room_id not in self._rooms or track is None:
            return

        for peer in self._rooms[room_id].values():
            try:
                senders = list(peer.pc.getSenders())
            except Exception as sender_error:
                logger.debug(f"peer sender enumeration failed: {sender_error}")
                continue
            for sender in senders:
                if (
                    _same_sender_track(getattr(sender, "track", None), track)
                    or peer.degraded_senders.get(sender) is track
                ):
                    peer.degraded_senders.pop(sender, None)
                    try:
                        peer.pc.removeTrack(sender)
                    except Exception as e:
                        logger.debug(
                            f"Could not remove ghost sender on {peer.peer_id}: {e}"
                        )
