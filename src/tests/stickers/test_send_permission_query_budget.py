"""Regression: the sticker send hot path must not recompute usage/pack counts.

``POST /stickers/{id}/send`` runs ``can_use_sticker`` followed by
``send_sticker``. Both only need a sticker's identity/pack and the pack's
authorization columns, but they used to call ``get_sticker`` (which aggregates
``sticker_usage`` with ``COUNT(u.id)``) and ``get_pack`` (which aggregates
``COUNT(s.id)``), and probed server membership twice. Those counts are pure
overhead on a write path and scale with the usage table's size.

This pins the query budget: the send path issues zero usage-count/sticker-count
aggregations and exactly one membership probe, while keeping the permission
decision byte-for-byte equivalent for every pack type.
"""

import uuid

from src.core.stickers import PackType, StickerFormat


def _register(auth, suffix, prefix):
    return auth.register(
        username=f"{prefix}_{suffix}",
        email=f"{prefix}_{suffix}@example.com",
        password="TestPass123!",
    )


def _sticker(stickers, user_id, pack_id, name):
    return stickers.add_sticker(
        user_id=user_id,
        pack_id=pack_id,
        name=name,
        format=StickerFormat.PNG,
        url="https://cdn.example.com/stickers/test.png",
        size=1000,
    )


def test_send_path_query_budget(db_and_modules):
    """can_use_sticker + send_sticker avoid all count JOINs and double probing."""
    bundle = db_and_modules
    auth, messaging, servers, stickers = (
        bundle.auth,
        bundle.messaging,
        bundle.servers,
        bundle.module,
    )
    suffix = uuid.uuid4().hex[:8]
    owner = _register(auth, suffix, "qb_owner")
    other = _register(auth, suffix, "qb_other")
    server = servers.create_server(owner.id, f"QB {suffix}")
    servers.add_member(server.id, other.id)
    pack = stickers.create_pack(user_id=owner.id, name="QB Pack", server_id=server.id)
    sticker = _sticker(stickers, owner.id, pack.id, "qb_sticker")
    dm = messaging.create_dm(owner.id, other.id)
    msg = messaging.send_message(owner.id, dm.id, "sticker!")

    counters = {"usage": 0, "packcount": 0, "membership": 0}
    orig_one, orig_all = stickers._db.fetch_one, stickers._db.fetch_all
    orig_member = stickers._is_server_member

    def _spy(sql, params=()):
        text = str(sql)
        if "COUNT(u.id) as usage_count" in text:
            counters["usage"] += 1
        if "COUNT(s.id) as sticker_count" in text:
            counters["packcount"] += 1

    def spy_one(sql, params=()):
        _spy(sql, params)
        return orig_one(sql, params)

    def spy_all(sql, params=()):
        _spy(sql, params)
        return orig_all(sql, params)

    def spy_member(uid, sid):
        counters["membership"] += 1
        return orig_member(uid, sid)

    stickers._db.fetch_one = spy_one
    stickers._db.fetch_all = spy_all
    stickers._is_server_member = spy_member
    try:
        assert stickers.can_use_sticker(owner.id, sticker.id) is True
        usage = stickers.send_sticker(owner.id, msg.id, sticker.id)
    finally:
        stickers._db.fetch_one = orig_one
        stickers._db.fetch_all = orig_all
        stickers._is_server_member = orig_member

    assert usage.sticker_id == sticker.id
    assert counters == {"usage": 0, "packcount": 0, "membership": 1}

    # The write still lands: reading the sticker afterwards shows the count.
    assert stickers.get_sticker(sticker.id).usage_count == 1


def test_can_use_sticker_semantics_preserved(db_and_modules):
    """The lightweight authz path keeps the original decision for each type."""
    bundle = db_and_modules
    auth, servers, stickers = bundle.auth, bundle.servers, bundle.module
    suffix = uuid.uuid4().hex[:8]
    owner = _register(auth, suffix, "sem_owner")
    outsider = _register(auth, suffix, "sem_out")
    server = servers.create_server(owner.id, f"SEM {suffix}")

    # SERVER pack: members only.
    server_pack = stickers.create_pack(
        user_id=owner.id, name="SEM server", server_id=server.id
    )
    server_sticker = _sticker(stickers, owner.id, server_pack.id, "sem_server")
    assert stickers.can_use_sticker(owner.id, server_sticker.id) is True
    assert stickers.can_use_sticker(outsider.id, server_sticker.id) is False

    # PERSONAL pack: owner only.
    personal_pack = stickers.create_pack(
        user_id=owner.id, name="SEM personal", pack_type=PackType.PERSONAL
    )
    personal_sticker = _sticker(stickers, owner.id, personal_pack.id, "sem_personal")
    assert stickers.can_use_sticker(owner.id, personal_sticker.id) is True
    assert stickers.can_use_sticker(outsider.id, personal_sticker.id) is False

    # DEFAULT pack: open to everyone.
    default_pack = stickers.create_pack(
        user_id=owner.id, name="SEM default", pack_type=PackType.DEFAULT
    )
    default_sticker = _sticker(stickers, owner.id, default_pack.id, "sem_default")
    assert stickers.can_use_sticker(owner.id, default_sticker.id) is True
    assert stickers.can_use_sticker(outsider.id, default_sticker.id) is True

    # Unknown sticker still denies.
    assert stickers.can_use_sticker(owner.id, 999999999) is False
