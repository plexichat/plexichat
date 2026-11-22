"""SECURITY: the core reaction write gate must resolve a channel-id alias.

``ReactionPermissionsMixin._get_channel_for_conversation`` resolves the backing
server channel with ``SELECT * FROM srv_channels WHERE conversation_id = ?``.
Every caller (``add_reaction``, ``remove_all_reactions``, ...) treats a miss as
"genuine DM -> skip the server-channel permission check".

That is wrong for the other id shape the messaging layer accepts: a server
channel's OWN ``id`` used in the conversation slot.  ``messages_crud`` stores
``msg_messages.conversation_id = srv_channels.id`` when the conversation link
is missing and cannot be repaired, and ``is_participant`` grants every server
member participation in that id via ``srv_channels.id`` (``WHERE conversation_id
= ? OR id = ?``) regardless of the per-channel permission.  A lookup keyed only
on ``conversation_id`` misses that alias, so a member denied
``messages.add_reactions`` on a private channel can still react there.

The lookup must resolve both id forms (``OR id = ?``) so the same live gate is
applied no matter which alias addressed the message.
"""

import uuid
from types import SimpleNamespace

import pytest

from src.core.reactions.exceptions import PermissionDeniedError


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.reactions]

CHANNEL_ID = 555  # the channel's OWN id, used as the message's conversation_id
BACKING_CONVERSATION = 777  # the conversation the channel is linked to
MESSAGE_ID = 20


def _users(auth_manager):
    tag = uuid.uuid4().hex[:8]
    author = auth_manager.register(
        username=f"alias_author_{tag}",
        email=f"alias_author_{tag}@example.com",
        password="TestPass123!",
    )
    viewer = auth_manager.register(
        username=f"alias_viewer_{tag}",
        email=f"alias_viewer_{tag}@example.com",
        password="TestPass123!",
    )
    return author, viewer


def _seed_alias_message(db, auth_manager, server_manager):
    """A message whose conversation_id is a server channel's own id (alias)."""
    author, viewer = _users(auth_manager)
    server = server_manager.create_server(author.id, f"Alias Server {uuid.uuid4().hex[:6]}")

    # The real linked conversation (srv_channels.conversation_id) ...
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, created_at, updated_at) VALUES (?, 'server', 1000, 1000)",
        (BACKING_CONVERSATION,),
    )
    # ... and the conversation row keyed by the channel's own id (the alias shape).
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, created_at, updated_at) VALUES (?, 'server', 1000, 1000)",
        (CHANNEL_ID,),
    )
    db.execute(
        "INSERT INTO srv_channels "
        "(id, server_id, conversation_id, name, channel_type, position, created_at, updated_at) "
        "VALUES (?, ?, ?, 'private', 'text', 0, 1000, 1000)",
        (CHANNEL_ID, server.id, BACKING_CONVERSATION),
    )
    # Server membership participates via srv_channels.id independent of
    # channels.view / messages.add_reactions; mirror that with a participant row.
    db.execute(
        "INSERT INTO msg_participants "
        "(id, conversation_id, user_id, role, joined_at) VALUES (1, ?, ?, 'member', 1000)",
        (CHANNEL_ID, viewer.id),
    )
    db.execute(
        "INSERT INTO msg_messages "
        "(id, conversation_id, author_id, content, created_at, updated_at, message_type) "
        "VALUES (?, ?, ?, 'private-secret', 1000, 1000, 'text')",
        (MESSAGE_ID, CHANNEL_ID, author.id),
    )
    return author, viewer, server


def _servers(allow_add_reactions: bool):
    """Servers stub: only the add-reactions bit varies."""

    def has_permission(uid, sid, permission, channel_id=None):
        if permission == "messages.add_reactions":
            return allow_add_reactions
        return True

    return SimpleNamespace(has_permission=has_permission, get_member=lambda *a: object())


def test_add_reaction_via_channel_id_alias_denied_without_permission(
    db, reaction_manager, auth_manager, server_manager
):
    """A member denied add_reactions cannot react via the channel-id alias."""
    _author, viewer, _server = _seed_alias_message(db, auth_manager, server_manager)
    reaction_manager._servers = _servers(allow_add_reactions=False)

    with pytest.raises(PermissionDeniedError) as exc:
        reaction_manager.add_reaction(viewer.id, MESSAGE_ID, ":denied:")

    assert exc.value.permission == "messages.add_reactions"


def test_add_reaction_via_channel_id_alias_allowed_with_permission(
    db, reaction_manager, auth_manager, server_manager
):
    """With the grant present, the alias still resolves to the real channel."""
    _author, viewer, server = _seed_alias_message(db, auth_manager, server_manager)
    seen = []

    def has_permission(uid, sid, permission, channel_id=None):
        seen.append((uid, sid, permission, channel_id))
        return permission != "messages.add_reactions" or channel_id == CHANNEL_ID

    reaction_manager._servers = SimpleNamespace(
        has_permission=has_permission, get_member=lambda *a: object()
    )

    reaction = reaction_manager.add_reaction(viewer.id, MESSAGE_ID, ":allowed:")

    assert reaction.emoji == ":allowed:"
    # The permission gate must have been evaluated against the *backing channel*,
    # not skipped as if this were a DM.
    assert (viewer.id, server.id, "messages.add_reactions", CHANNEL_ID) in seen


def test_add_reaction_in_genuine_dm_still_allowed(db, reaction_manager, auth_manager):
    """A conversation with no backing channel stays a DM and needs no gate."""
    _author, viewer = _users(auth_manager)
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, created_at, updated_at) VALUES (?, 'dm', 1000, 1000)",
        (999,),
    )
    db.execute(
        "INSERT INTO msg_participants "
        "(id, conversation_id, user_id, role, joined_at) VALUES (1, 999, ?, 'member', 1000)",
        (viewer.id,),
    )
    db.execute(
        "INSERT INTO msg_messages "
        "(id, conversation_id, author_id, content, created_at, updated_at, message_type) "
        "VALUES (?, 999, ?, 'dm-secret', 1000, 1000, 'text')",
        (MESSAGE_ID, _author.id),
    )
    reaction_manager._servers = _servers(allow_add_reactions=False)

    reaction = reaction_manager.add_reaction(viewer.id, MESSAGE_ID, ":dmok:")

    assert reaction.emoji == ":dmok:"


def test_reverse_lookup_resolves_both_id_forms(
    db, reaction_manager, auth_manager, server_manager
):
    """Direct pin: the backing-channel lookup itself handles either id form."""
    _author, _viewer, server = _seed_alias_message(db, auth_manager, server_manager)

    by_channel_id = reaction_manager._get_channel_for_conversation(CHANNEL_ID)
    by_conversation_id = reaction_manager._get_channel_for_conversation(
        BACKING_CONVERSATION
    )

    assert by_channel_id["id"] == CHANNEL_ID
    assert by_channel_id["server_id"] == server.id
    assert by_conversation_id["id"] == CHANNEL_ID
