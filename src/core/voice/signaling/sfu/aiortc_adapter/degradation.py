"""Degradation hints the aiortc backend can honour."""

from __future__ import annotations

from typing import Dict

import utils.logger as logger

from .media import BoundedVideoTrack
from .models import AiortcPeer


class DegradationMixin:
    """Layer hints, applied as a bounded video frame rate."""

    _rooms: Dict[str, Dict[str, AiortcPeer]]

    @staticmethod
    def _bound_video_senders(peer: AiortcPeer) -> bool:
        """Cap the frame rate of every outbound video track for ``peer``.

        The nearest lever to a lower simulcast layer on a backend that
        negotiates a single encoding and has no bitrate API: the peer keeps
        its video -- bounded, instead of losing it entirely or being handed a
        frozen picture. Audio is never touched. Returns whether any sender
        changed state.
        """
        changed = False
        try:
            senders = list(peer.pc.getSenders())
        except Exception as exc:
            logger.debug(f"Could not enumerate senders for {peer.peer_id}: {exc}")
            return False
        for sender in senders:
            track = getattr(sender, "track", None)
            if track is None or getattr(track, "kind", None) != "video":
                continue
            if sender in peer.degraded_senders or isinstance(track, BoundedVideoTrack):
                continue
            try:
                sender.replaceTrack(BoundedVideoTrack(track))
            except Exception as exc:
                logger.debug(f"Could not bound video sender on {peer.peer_id}: {exc}")
                continue
            peer.degraded_senders[sender] = track
            changed = True
        return changed

    @staticmethod
    def _restore_video_senders(peer: AiortcPeer) -> bool:
        """Re-attach full-rate video for every sender a hint degraded."""
        changed = False
        for sender, track in list(peer.degraded_senders.items()):
            try:
                sender.replaceTrack(track)
            except Exception as exc:
                logger.debug(f"Could not restore video sender on {peer.peer_id}: {exc}")
                continue
            peer.degraded_senders.pop(sender, None)
            changed = True
        return changed

    async def set_preferred_layers(
        self,
        room_id: str,
        peer_id: str,
        consumer_id: str,
        spatial_layer: int,
        temporal_layer: int,
    ) -> bool:
        """Apply a degradation hint as far as aiortc allows, or report False.

        aiortc exposes no simulcast API and no bitrate control -- a peer
        connection negotiates a single video encoding and ``RTCRtpSender``
        has no ``setParameters`` -- so the hint is applied as a *bounded*
        frame rate rather than an all-or-nothing stop: a degraded peer keeps
        its video at a low rate (the stream the estimator is reacting to)
        while the encoded bitrate falls, and full rate is re-attached when
        the hint is lifted. Audio is never touched. The hint is reported as
        applied only when a sender actually changed state, so a peer with no
        video -- or one already in the requested state -- still gets an
        honest ``False`` instead of the silent success that made the old
        implementation a lie.
        """
        peer = self._rooms.get(room_id, {}).get(peer_id)
        if peer is None:
            logger.debug(
                f"Preferred layers not applied for consumer {consumer_id}: "
                f"peer {peer_id} is not in room {room_id}"
            )
            return False
        if spatial_layer <= 0:
            applied = self._bound_video_senders(peer)
            action = "bounded"
        else:
            applied = self._restore_video_senders(peer)
            action = "restored"
        if not applied:
            logger.debug(
                f"Preferred layers not applied for consumer {consumer_id}: "
                f"no video sender to change (spatial={spatial_layer}, "
                f"temporal={temporal_layer})"
            )
        else:
            logger.info(
                f"Preferred layers applied for consumer {consumer_id}: "
                f"video {action} (spatial={spatial_layer}, "
                f"temporal={temporal_layer})"
            )
        return applied
