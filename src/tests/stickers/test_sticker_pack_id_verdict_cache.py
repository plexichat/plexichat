"""Repeated sticker-use checks must not re-read a sticker's pack id.

``POST /stickers/{id}/send`` calls ``can_use_sticker`` followed by
``send_sticker``. Both resolve the sticker's pack with the same
``SELECT pack_id FROM sticker_stickers WHERE id = ?``; the second lookup is a
pure duplicate of the first on a write hot path (prior work already removed the
count JOINs and the double membership probe). The pack id is immutable for a
sticker's lifetime, so a bounded, short-TTL, mutation-invalidated memoization is
safe and removes the per-use duplicate.

These tests pin:

* a warm pack-id entry serves repeated use checks with **no** catalog SQL;
* a sticker deletion invalidates the entry so the verdict fails closed.
"""

import uuid

from src.core.stickers import StickerFormat


def _bundle(db_and_modules):
    b = db_and_modules
    return b.auth, b.messaging, b.servers, b.module


def _reg(auth, prefix):
    s = uuid.uuid4().hex[:8]
    return auth.register(
        username=f"{prefix}_{s}",
        email=f"{prefix}_{s}@example.com",
        password="TestPass123!",
    )


def _sticker(stickers, owner_id, pack_id, name):
    return stickers.add_sticker(
        user_id=owner_id,
        pack_id=pack_id,
        name=name,
        format=StickerFormat.PNG,
        url="https://cdn.example.com/stickers/x.png",
        size=1000,
    )


def _pack_id_queries(stickers, monkeypatch):
    """Count only the lightweight sticker-to-pack existence lookup."""
    real = stickers._db.fetch_one
    counts = {"n": 0}

    def spy(sql, params=()):
        if "SELECT pack_id FROM sticker_stickers WHERE id = ?" in str(sql):
            counts["n"] += 1
        return real(sql, params)

    monkeypatch.setattr(stickers._db, "fetch_one", spy)
    return counts


def test_repeated_use_reuses_pack_id_lookup(db_and_modules, monkeypatch):
    auth, messaging, servers, stickers = _bundle(db_and_modules)
    owner = _reg(auth, "pid_owner")
    other = _reg(auth, "pid_other")
    server = servers.create_server(owner.id, "PackIdServer")
    servers.add_member(server.id, other.id)
    pack = stickers.create_pack(user_id=owner.id, name="PID", server_id=server.id)
    sticker = _sticker(stickers, owner.id, pack.id, "pid_sticker")
    dm = messaging.create_dm(owner.id, other.id)
    msg = messaging.send_message(owner.id, dm.id, "sticker!")

    counts = _pack_id_queries(stickers, monkeypatch)

    # Cold path resolves the pack id once (can_use_sticker) and send reuses it.
    assert stickers.can_use_sticker(owner.id, sticker.id) is True
    stickers.send_sticker(owner.id, msg.id, sticker.id)
    cold = counts["n"]

    # Warm path: neither the permission check nor the send re-reads the row.
    counts["n"] = 0
    assert stickers.can_use_sticker(owner.id, sticker.id) is True
    stickers.send_sticker(owner.id, msg.id, sticker.id)
    warm = counts["n"]

    assert cold == 1, cold
    assert warm == 0, warm


def test_deletion_invalidates_cached_pack_id(db_and_modules, monkeypatch):
    auth, _messaging, servers, stickers = _bundle(db_and_modules)
    owner = _reg(auth, "pid_del")
    server = servers.create_server(owner.id, "PackIdDelServer")
    pack = stickers.create_pack(user_id=owner.id, name="PIDDel", server_id=server.id)
    sticker = _sticker(stickers, owner.id, pack.id, "pid_del_sticker")

    # Warm the verdict, then delete the sticker out from under the cache.
    assert stickers.can_use_sticker(owner.id, sticker.id) is True
    assert stickers.delete_sticker(owner.id, sticker.id) is True

    assert stickers.can_use_sticker(owner.id, sticker.id) is False
