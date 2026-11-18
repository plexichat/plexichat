"""Cache-coherency regressions for emoji reads and the ICE bootstrap.

The emoji list/detail caches previously used a default key whose shape did not
match their invalidation glob (FastAPI passes route params as kwargs, so the
real key is ``<prefix>:current_user:TokenInfo:<uid>:server_id:str:<sid>`` while
invalidation looked for ``<prefix>:str:<sid>:*``). Mutations therefore never
evicted anything and a stale 200 — including one for a revoked member — was
served for the full 300s/3600s TTL. These tests pin the corrected behaviour:
keys are credential-partitioned, every mutation path can evict them, and two
credentials never share an entry.
"""

import asyncio
from types import SimpleNamespace

from src.core.database.cache.base import mem_cache_matching_keys


class TokenInfo:
    """Minimal stand-in with the real class name ``TokenInfo``.

    ``generate_cache_key`` special-cases that class name to key on ``user_id``;
    ``authenticated_cache_key`` additionally reads ``token_type``/``account_id``.
    """

    def __init__(self, user_id, token_type="user", account_id=None):
        self.user_id = user_id
        self.token_type = token_type
        self.account_id = account_id


def _fake_emoji(emoji_id=9, name="hello"):
    return SimpleNamespace(
        id=emoji_id,
        server_id=123,
        name=name,
        animated=False,
        url="",
        available=True,
        created_by=7,
        uploader_username="tester",
        created_at=1,
    )


class _FakeReactions:
    def __init__(self, emojis):
        self.emojis = list(emojis)
        self.list_calls = 0
        self.detail_calls = 0

    def get_server_custom_emojis(self, sid):
        self.list_calls += 1
        return list(self.emojis)

    def get_custom_emoji(self, eid):
        self.detail_calls += 1
        return next((e for e in self.emojis if e.id == eid), None)


class _FakeServers:
    """Membership gate that admits the two user ids used below."""

    def get_member(self, sid, uid):
        return object() if uid in (7, 8) else None


def _wire(monkeypatch, reactions, servers=None):
    from src.api.routes import emojis

    monkeypatch.setattr(emojis.api, "get_reactions", lambda: reactions)
    monkeypatch.setattr(emojis.api, "get_servers", lambda: servers or _FakeServers())
    return emojis


def _field(payload, name):
    """A cached hit reconstructs to a dict (schema not on the allowlist)."""
    return payload.get(name) if isinstance(payload, dict) else getattr(payload, name)


class TestEmojiCacheKeys:
    def test_keys_partition_by_credential_and_resource(self):
        from src.api.routes import emojis

        user7 = TokenInfo(7)
        user8 = TokenInfo(8)
        bot_of_7 = TokenInfo(7, token_type="bot", account_id=99)

        list_7 = emojis._emoji_list_cache_key(server_id="123", current_user=user7)
        list_8 = emojis._emoji_list_cache_key(server_id="123", current_user=user8)
        list_bot = emojis._emoji_list_cache_key(server_id="123", current_user=bot_of_7)
        detail_7 = emojis._emoji_detail_cache_key(
            server_id="123", emoji_id="9", current_user=user7
        )

        # Distinct users, and a bot vs its owner, never share a key.
        assert len({list_7, list_8, list_bot}) == 3
        assert ":sid:123:" in list_7
        assert ":eid:9:" in detail_7
        assert detail_7.startswith(emojis._EMOJI_DETAIL_CACHE_PREFIX)
        assert list_7.startswith(emojis._EMOJI_LIST_CACHE_PREFIX)


class TestEmojiCacheCoherency:
    def test_invalidation_reaches_real_cache_keys(self, monkeypatch):
        """A mutation must evict the exact keys the reads stored."""
        emojis = _wire(monkeypatch, _FakeReactions([_fake_emoji()]))

        asyncio.run(
            emojis.get_server_emojis(server_id="123", current_user=TokenInfo(7))
        )
        asyncio.run(
            emojis.get_emoji(server_id="123", emoji_id="9", current_user=TokenInfo(7))
        )

        live = mem_cache_matching_keys(f"{emojis._EMOJI_LIST_CACHE_PREFIX}:sid:123:*")
        assert live, "the read path should have populated the cache"
        assert mem_cache_matching_keys(
            f"{emojis._EMOJI_DETAIL_CACHE_PREFIX}:sid:123:eid:9:*"
        )

        emojis._invalidate_emoji_cache(server_id=123, emoji_id=9)

        assert (
            mem_cache_matching_keys(f"{emojis._EMOJI_LIST_CACHE_PREFIX}:sid:123:*")
            == []
        )
        assert (
            mem_cache_matching_keys(
                f"{emojis._EMOJI_DETAIL_CACHE_PREFIX}:sid:123:eid:9:*"
            )
            == []
        )

    def test_identical_output_then_fresh_after_invalidation(self, monkeypatch):
        reactions = _FakeReactions([_fake_emoji(name="before")])
        emojis = _wire(monkeypatch, reactions)
        token = TokenInfo(7)

        first = asyncio.run(
            emojis.get_server_emojis(server_id="123", current_user=token)
        )
        # Data changes behind the cache's back.
        reactions.emojis = [_fake_emoji(name="after")]
        cached = asyncio.run(
            emojis.get_server_emojis(server_id="123", current_user=token)
        )
        # Warm reads are byte-for-byte the cached payload and hit no backend.
        assert _field(first[0], "name") == _field(cached[0], "name") == "before"
        assert reactions.list_calls == 1

        emojis._invalidate_emoji_cache(server_id=123)
        fresh = asyncio.run(
            emojis.get_server_emojis(server_id="123", current_user=token)
        )
        assert _field(fresh[0], "name") == "after"
        assert reactions.list_calls == 2

    def test_no_cross_credential_cache_reuse(self, monkeypatch):
        reactions = _FakeReactions([_fake_emoji()])
        emojis = _wire(monkeypatch, reactions)

        for token in (
            TokenInfo(7),
            TokenInfo(7),  # same user -> cache hit, no extra backend call
            TokenInfo(8),  # another user -> its own entry
            TokenInfo(7, token_type="bot", account_id=99),  # bot namespace
        ):
            asyncio.run(emojis.get_server_emojis(server_id="123", current_user=token))
        assert reactions.list_calls == 3


class TestIceServersCredentialPartition:
    def test_bot_and_owner_do_not_share_cached_credentials(self, monkeypatch):
        from src.api.routes import voice
        from src.core.voice import signaling

        calls = []

        def _fake_build(user_id):
            calls.append(user_id)
            return []

        monkeypatch.setattr(signaling, "get_ice_servers", _fake_build)

        owner = TokenInfo(7)
        bot = TokenInfo(7, token_type="bot", account_id=99)
        for token in (owner, owner, bot):
            asyncio.run(voice.get_ice_servers(current_user=token))

        # Two distinct credentials -> two cold builds; the repeated owner hit
        # is served from cache. Before partitioning all three shared one key.
        assert calls == [7, 7]
