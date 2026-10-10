"""
Regression tests for the second sticker audit fixes.

Covers:
- K1: ``can_use_sticker`` must deny PERSONAL stickers to non-owners and allow
  DEFAULT to anyone.
- K2: a SERVER pack with a NULL server_id (legacy/orphaned row) can only be
  deleted by its creator (previously any user could delete it).
- K3: ``can_use_sticker`` returns False for a sticker the caller may not use,
  which is the gate the send-sticker route now enforces.
- K4: ``get_sticker_suggestions`` must not surface a server's stickers to
  non-members.
- K5: deleting a sticker the caller cannot access is rejected (the route maps
  this to 404, never a silent success / 500).
"""

import time

import pytest

from src.core.stickers.models import PackType, StickerFormat
from src.core.stickers.exceptions import (
    PermissionDeniedError,
    PackNotFoundError,
)


def _personal_sticker(sticker_manager, owner_id, name="wave"):
    pack = sticker_manager.create_pack(
        owner_id, "Personal Pack", pack_type=PackType.PERSONAL
    )
    return sticker_manager.add_sticker(
        owner_id, pack.id, name, StickerFormat.PNG, "https://x/y.png", 10
    )


def _insert_server_pack_row(sticker_manager, pack_id, created_by, server_id):
    now = int(time.time() * 1000)
    sticker_manager._db.execute(
        "INSERT INTO sticker_packs "
        "(id, name, description_encrypted, pack_type, server_id, created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pack_id, "Orphan Server", None, "server", server_id, created_by, now, now),
    )
    sticker_manager._db.execute(
        "INSERT INTO sticker_stickers "
        "(id, pack_id, name, format, url, size, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pack_id + 100000, pack_id, "s", "png", "https://x/s.png", 10, now),
    )
    return pack_id + 100000


def test_k1_personal_sticker_denied_to_non_owner(sticker_manager, two_users):
    user1, user2 = two_users
    sticker = _personal_sticker(sticker_manager, user1.id)
    assert sticker_manager.can_use_sticker(user1.id, sticker.id) is True
    assert sticker_manager.can_use_sticker(user2.id, sticker.id) is False


def test_k1_default_sticker_allowed_to_anyone(sticker_manager, two_users):
    user1, user2 = two_users
    default = sticker_manager.create_pack(
        user1.id, "Default", pack_type=PackType.DEFAULT
    )
    sticker = sticker_manager.add_sticker(
        user1.id, default.id, "def", StickerFormat.PNG, "https://x/d.png", 10
    )
    assert sticker_manager.can_use_sticker(user2.id, sticker.id) is True


def test_k2_null_server_pack_delete_requires_owner(sticker_manager, two_users):
    user1, user2 = two_users
    # Legacy SERVER pack whose server_id is NULL — the authz gap. ``create_pack``
    # rejects this shape, so simulate the orphaned row directly.
    sticker_id = _insert_server_pack_row(sticker_manager, 900001, user1.id, None)

    # Owner can delete.
    assert sticker_manager.delete_sticker(user1.id, sticker_id) is True

    # A different user is rejected.
    sticker_id2 = _insert_server_pack_row(sticker_manager, 900002, user1.id, None)
    with pytest.raises(PermissionDeniedError):
        sticker_manager.delete_sticker(user2.id, sticker_id2)


def test_k3_can_use_sticker_false_blocks_send(sticker_manager, two_users):
    user1, user2 = two_users
    sticker = _personal_sticker(sticker_manager, user1.id)
    # The send-sticker route relies on this returning False for non-owners.
    assert sticker_manager.can_use_sticker(user2.id, sticker.id) is False


def test_k4_suggestions_hide_server_stickers_from_non_members(
    sticker_manager, server_manager, two_users
):
    owner, stranger = two_users
    server = server_manager.create_server(owner.id, "Sticker Server")

    pack = sticker_manager.create_pack(
        owner.id, "Server Pack", pack_type=PackType.SERVER, server_id=server.id
    )
    server_sticker = sticker_manager.add_sticker(
        owner.id, pack.id, "kitten", StickerFormat.PNG, "https://x/k.png", 10
    )

    # Owner (member) sees the server sticker when scoped to the server.
    member_hits = sticker_manager.get_sticker_suggestions(
        owner.id, "kitten", server_id=server.id
    )
    assert server_sticker.id in [h.sticker.id for h in member_hits]

    # A non-member must NOT receive the server-only sticker.
    stranger_hits = sticker_manager.get_sticker_suggestions(
        stranger.id, "kitten", server_id=server.id
    )
    assert server_sticker.id not in [h.sticker.id for h in stranger_hits]


def test_k5_delete_inaccessible_sticker_rejected(sticker_manager, two_users):
    user1, user2 = two_users
    sticker = _personal_sticker(sticker_manager, user1.id)
    # Manager rejects the attempt; the route converts this into a 404 via
    # PackNotFoundError, never a silent success or 500.
    with pytest.raises((PermissionDeniedError, PackNotFoundError)):
        sticker_manager.delete_sticker(user2.id, sticker.id)
