"""Live ``messages.send`` gate for poll writes.

Poll reads need a live ``channels.view`` grant (see :mod:`reading`). Writes are
stronger: conversation participation for a server channel is granted to every
member of the server, and ``channels.view`` says nothing about the right to
post there. Without this gate a member who can view a read-only channel can
vote on, change, remove, create, close or delete polls in a channel they are
not allowed to post to -- mutating recorded votes and tallies that every
viewer sees. The REST send, forwarding and voice paths already add the same
independent ``messages.send`` requirement.

Kept in its own module so the read gate in :mod:`reading` stays focused and the
two decisions (view for reads, send for writes) can never be conflated.
"""

from ...base import SnowflakeID
from ..exceptions import PermissionDeniedError
from .reading import _can_view_poll_message


def _get_servers_module():
    """Resolve the servers module lazily (kept separate so it is patchable)."""
    import src.api as api

    return api.get_servers()


def can_post_poll_message(
    messaging, message_id: SnowflakeID, user_id: SnowflakeID
) -> bool:
    """True when the caller may post in the poll's backing channel.

    Fails closed: any lookup that cannot be resolved denies the write. A
    conversation with no backing server channel is a genuine DM/group and
    needs no further gate -- participation is the only boundary there.
    """
    msg = messaging.get_message(user_id, message_id)
    if not msg:
        return False

    if isinstance(msg, dict):
        conversation_id = msg.get("conversation_id")
    else:
        conversation_id = getattr(msg, "conversation_id", None)
    if not conversation_id:
        return True  # DM/group: participation is the only boundary

    servers = _get_servers_module()
    if servers is None:
        # The backing-channel lookup cannot be resolved without the servers
        # module. Deny the write rather than skipping the messages.send gate.
        return False
    if getattr(servers, "_setup_complete", True) is False:
        return True  # present but uninitialized: no server-channel store exists
    lookup = getattr(servers, "get_channel_by_conversation", None)
    if not callable(lookup):
        return False
    try:
        info = lookup(conversation_id)
    except Exception:
        return False
    if info is None:
        # No reverse row keyed by conversation_id is normally a genuine
        # DM/group. It does NOT prove one: a channel id used in place of its
        # linked conversation id has no such row either. Probe
        # membership-agnostically and require messages.send when the id is
        # actually a server channel, mirroring the read gate's alias handling.
        probe = getattr(servers, "channel_exists", None)
        if not callable(probe):
            probe = getattr(servers, "get_channel_conversation_id", None)
        if callable(probe):
            try:
                is_channel = bool(probe(conversation_id))
            except Exception:
                return False  # cannot prove it is a DM -> deny
            if is_channel:
                return _require_channel_send(servers, conversation_id, user_id)
        return True  # genuine DM/group conversation
    channel_id = info.get("id") if isinstance(info, dict) else None
    server_id = info.get("server_id") if isinstance(info, dict) else None
    if channel_id is None or server_id is None:
        return False
    return _require_channel_send(servers, channel_id, user_id, server_id)


def _require_channel_send(servers, channel_id, user_id, server_id=None) -> bool:
    """Evaluate ``messages.send`` for one channel; fail closed on any error."""
    if server_id is None:
        # Channel-id alias path: no reverse row supplies the server id. The
        # authoritative ``get_channel`` (already required for view by the read
        # gate) yields the channel with its server id for a viewer who passed.
        get_channel = getattr(servers, "get_channel", None)
        if not callable(get_channel):
            return False
        try:
            channel = get_channel(channel_id, user_id)
        except Exception:
            return False
        if channel is None:
            return False
        server_id = (
            channel.get("server_id")
            if isinstance(channel, dict)
            else getattr(channel, "server_id", None)
        )
    if server_id is None:
        return False
    has_permission = getattr(servers, "has_permission", None)
    if not callable(has_permission):
        return False
    try:
        return bool(has_permission(user_id, server_id, "messages.send", channel_id))
    except Exception:
        return False


def require_poll_post_access(manager, message_id: SnowflakeID, user_id: SnowflakeID):
    """Raise :class:`PermissionDeniedError` unless the caller may post here.

    Call from every poll mutation *before* issuing any write so a read-only
    channel viewer can never change recorded poll state in that channel.
    """
    messaging = manager._require_messaging()
    # Every poll scope still requires the live view grant the reads use. The
    # write paths that already called get_poll pay for this twice; keeping it
    # here covers create_poll, which has no poll row to gate via get_poll.
    if not _can_view_poll_message(messaging, message_id, user_id):
        raise PermissionDeniedError("Cannot view the poll's channel")
    if not can_post_poll_message(messaging, message_id, user_id):
        raise PermissionDeniedError(
            "Missing messages.send permission for the poll's channel"
        )
