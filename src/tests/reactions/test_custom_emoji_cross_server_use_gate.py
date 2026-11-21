"""A custom emoji may only be used by a member of the server that owns it.

``ReactionOpsMixin.add_reaction`` scoped a custom emoji to the channel's
server, but a DM has no ``srv_channels`` row so that branch never ran. The
only remaining check was "does this emoji id exist and is it available", which
let any authenticated caller react with an emoji owned by a server they are
not a member of (including private servers) simply by knowing its id.

The fix gates custom-emoji use on membership of the *owning* server in every
context, DMs included, and fails closed when membership cannot be verified.
"""

import struct
import uuid
import zlib

import pytest

from src.core.reactions import CustomEmojiNotFoundError


def _png_bytes() -> bytes:
    """A minimal valid 1x1 PNG (passes PIL.verify)."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _make_user(modules, prefix: str):
    suffix = uuid.uuid4().hex[:8]
    return modules.auth.register(
        username=f"{prefix}_{suffix}",
        email=f"{prefix}_{suffix}@example.com",
        password="TestPass123!",
    )


def test_dm_reaction_with_foreign_server_emoji_is_rejected(modules):
    """Attacker who is not in the emoji's server cannot use it in a DM."""
    owner = _make_user(modules, "emoji_owner")
    attacker = _make_user(modules, "emoji_attacker")

    server = modules.servers.create_server(owner.id, "EmojiSource")
    emoji = modules.reactions.create_custom_emoji(
        owner.id, server.id, "secretemoji", _png_bytes(), "image/png"
    )

    # The attacker is a DM participant (so add_reaction's participant + server
    # permission gates are satisfied) but is NOT a member of the emoji's server.
    dm = modules.messaging.create_dm(attacker.id, owner.id)
    msg = modules.messaging.send_message(attacker.id, dm.id, "look at this")
    assert modules.servers.get_member(server.id, attacker.id) is None

    with pytest.raises(CustomEmojiNotFoundError):
        modules.reactions.add_reaction(
            attacker.id, msg.id, f"<:secretemoji:{emoji.id}>"
        )


def test_dm_reaction_with_own_server_emoji_still_allowed(modules):
    """A member of the owning server may still use its emoji in a DM."""
    owner = _make_user(modules, "emoji_owner2")
    member = _make_user(modules, "emoji_member2")

    server = modules.servers.create_server(owner.id, "EmojiSource2")
    modules.servers.add_member(server.id, member.id)
    emoji = modules.reactions.create_custom_emoji(
        owner.id, server.id, "myemoji", _png_bytes(), "image/png"
    )

    dm = modules.messaging.create_dm(member.id, owner.id)
    msg = modules.messaging.send_message(member.id, dm.id, "hi")

    reaction = modules.reactions.add_reaction(
        member.id, msg.id, f"<:myemoji:{emoji.id}>"
    )
    assert reaction.custom_emoji_id == emoji.id
