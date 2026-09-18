"""
Real Valkey integration tests.

``src/tests/test_valkey.py`` covers the client against ``FakeGlideClient``,
an in-memory stand-in for the GLIDE API. That catches logic errors in the
mixins but not disagreements with the server: type coercion, SCAN cursors,
Lua script semantics and pub/sub delivery only exist once a real Valkey is
answering. These tests exercise those paths against a live instance.

The ``valkey_client`` / ``async_valkey_client`` fixtures start a throwaway
container (or use ``VALKEY_HOST``/``VALKEY_PORT``) and SKIP VISIBLY when no
server can be reached, so the suite is safe to run anywhere.
"""

import asyncio
import time

import pytest

pytestmark = [pytest.mark.needs_valkey, pytest.mark.integration]


class TestRealValkeyBasics:
    """String keyspace against a live server."""

    def test_set_get_delete_roundtrip(self, valkey_client):
        assert valkey_client.set("greeting", "hello") is True
        assert valkey_client.get("greeting") == "hello"

        # Overwriting an existing key is a plain SET, not an error.
        assert valkey_client.set("greeting", "goodbye") is True
        assert valkey_client.get("greeting") == "goodbye"

        assert valkey_client.delete("greeting") == 1
        assert valkey_client.get("greeting") is None

    def test_missing_key_is_none_not_error(self, valkey_client):
        assert valkey_client.get("never-written") is None
        assert valkey_client.delete("never-written") == 0

    def test_ttl_round_trip_and_expiry(self, valkey_client):
        assert valkey_client.set("short", "lived", ttl=30) is True
        remaining = valkey_client.ttl("short")
        assert 0 < remaining <= 30

        # A key without a TTL reports -1; a missing key reports -2.
        valkey_client.set("forever", "value")
        assert valkey_client.ttl("forever") == -1
        assert valkey_client.ttl("absent") == -2

        assert valkey_client.expire("forever", 45) is True
        assert 0 < valkey_client.ttl("forever") <= 45

    def test_exists_is_boolean(self, valkey_client):
        valkey_client.set("present", "v")
        assert valkey_client.exists("present") is True
        assert valkey_client.exists("missing") is False

    def test_binary_values_survive_the_server(self, valkey_client):
        payload = bytes(range(256))
        assert valkey_client.set_bin("blob", payload) is True
        assert valkey_client.get_bin("blob") == payload

    def test_keys_are_prefixed_and_stripped(self, valkey_client):
        valkey_client.set("alpha", "1")
        valkey_client.set("beta", "2")

        names = valkey_client.keys("*")
        assert "alpha" in names
        assert "beta" in names
        # The prefix is a storage detail; callers never see it.
        assert all(not name.startswith("pytest:") for name in names)

        pattern = valkey_client.keys("al*")
        assert pattern == ["alpha"]

    def test_scan_walks_every_page(self, valkey_client):
        """``keys`` pages through SCAN; the double only ever returns one page.

        A cursor-termination bug (comparing a ``str`` cursor against the
        ``bytes`` the driver returns, say) would silently truncate the
        result at the first batch on a real server.
        """
        expected = {f"bulk:{i}" for i in range(250)}
        for name in expected:
            valkey_client.set(name, "v")

        assert set(valkey_client.keys("bulk:*")) == expected
        assert valkey_client.flush_prefix() == len(expected)

    def test_flush_prefix_removes_only_our_keys(self, valkey_client):
        valkey_client.set("one", "1")
        valkey_client.set("two", "2")
        assert valkey_client.flush_prefix() == 2
        assert valkey_client.keys("*") == []

    def test_health_check_reports_live_latency(self, valkey_client):
        health = valkey_client.health_check()
        assert health["enabled"] is True
        assert health["connected"] is True
        assert health["responsive"] is True
        assert health["latency_ms"] is not None


class TestRealValkeyDataStructures:
    """Hash, list, set, counter and JSON commands on a real server."""

    def test_hash_round_trip(self, valkey_client):
        assert valkey_client.hset("h", "name", "Alice") == 1
        assert valkey_client.hset("h", "name", "Alice") == 0  # no new field

        valkey_client.hmset("h", {"email": "alice@example.com", "age": 30})
        assert valkey_client.hget("h", "name") == "Alice"

        everything = valkey_client.hgetall("h")
        assert everything["email"] == "alice@example.com"

        assert valkey_client.hdel("h", "age") == 1
        assert valkey_client.hget("h", "age") is None

        # A hash with no fields left is gone from the keyspace.
        assert valkey_client.delete("h") == 1

    def test_list_round_trip(self, valkey_client):
        valkey_client.rpush("queue", "a", "b", "c")
        assert valkey_client.llen("queue") == 3
        assert valkey_client.lrange("queue", 0, -1) == ["a", "b", "c"]

        valkey_client.lpush("queue", "z")
        assert valkey_client.lpop("queue") == "z"
        assert valkey_client.rpop("queue") == "c"

        assert valkey_client.ltrim("queue", 0, 0) is True
        assert valkey_client.lrange("queue", 0, -1) == ["a"]

    def test_set_round_trip(self, valkey_client):
        assert valkey_client.sadd("tags", "python", "valkey") == 2
        assert valkey_client.sadd("tags", "python") == 0  # already a member

        assert valkey_client.sismember("tags", "python") is True
        assert valkey_client.sismember("tags", "java") is False
        assert valkey_client.smembers("tags") == {"python", "valkey"}

        assert valkey_client.srem("tags", "java") == 0
        assert valkey_client.srem("tags", "python") == 1

    def test_counter_is_atomic_across_calls(self, valkey_client):
        assert valkey_client.incr("count") == 1
        assert valkey_client.incr("count") == 2
        assert valkey_client.incr("count", 5) == 7
        assert valkey_client.decr("count", 2) == 5

    def test_json_round_trip(self, valkey_client):
        payload = {"name": "Alice", "tags": ["a", "b"], "nested": {"n": 1}}
        assert valkey_client.set_json("doc", payload) is True
        assert valkey_client.get_json("doc") == payload

        valkey_client.set_json("doc2", {"name": "Bob"})
        bulk = valkey_client.mget_json(["doc", "doc2", "absent"])
        assert set(bulk) == {"doc", "doc2"}
        assert bulk["doc2"] == {"name": "Bob"}

    def test_mget_json_empty_selection(self, valkey_client):
        assert valkey_client.mget_json([]) == {}

    def test_non_string_scalars_are_encodable(self, valkey_client):
        """``ValkeyValue`` promises int/float support; GLIDE only sends bytes.

        A raw int reaching the driver raises ``TypeError: Unsupported
        argument type: <class 'int'>``, which the in-memory double never
        reproduced because it stringifies whatever it is handed.
        """
        assert valkey_client.set("int", 7) is True
        assert valkey_client.get("int") == "7"
        assert valkey_client.set("float", 1.5) is True
        assert valkey_client.get("float") == "1.5"

        valkey_client.rpush("list", 1, 2, "three")
        assert valkey_client.lrange("list", 0, -1) == ["1", "2", "three"]

        assert valkey_client.sadd("set", 1, "two") == 2
        assert valkey_client.sismember("set", 1) is True

        valkey_client.hset("hash", "n", 42)
        assert valkey_client.hget("hash", "n") == "42"


class TestRealValkeyLocks:
    """Distributed locks, which depend on server-side conditional SET."""

    def test_acquire_and_release(self, valkey_client):
        token = valkey_client.acquire_lock("job", timeout=1.0)
        assert token is not None
        assert valkey_client.release_lock("job", token) is True
        # Released means acquirable again straight away.
        assert valkey_client.acquire_lock("job", timeout=1.0) is not None

    def test_second_holder_is_locked_out(self, valkey_client):
        first = valkey_client.acquire_lock("busy", timeout=1.0)
        assert first is not None

        start = time.time()
        assert valkey_client.acquire_lock("busy", timeout=0.3) is None
        assert time.time() - start >= 0.2

        valkey_client.release_lock("busy", first)

    def test_wrong_token_cannot_release(self, valkey_client):
        token = valkey_client.acquire_lock("owned", timeout=1.0)
        assert token is not None

        assert valkey_client.release_lock("owned", "not-the-token") is False
        # Still held: the failed release must not have deleted the lock.
        assert valkey_client.acquire_lock("owned", timeout=0.2) is None

        assert valkey_client.release_lock("owned", token) is True

    def test_lock_expires_with_its_ttl(self, valkey_client):
        token = valkey_client.acquire_lock("ttl-lock", timeout=1.0, lock_timeout=200)
        assert token is not None
        time.sleep(0.5)
        # The lock's own TTL elapsed, so anyone may take it now.
        assert valkey_client.acquire_lock("ttl-lock", timeout=0.5) is not None


class TestRealValkeyPubSub:
    """Pub/sub against a real server."""

    def test_publish_and_receive(self, valkey_client):
        valkey_client.subscribe("events")

        # Give the server a moment to register the subscription, then publish
        # until it lands: the subscriber count is only observable after the
        # SUBSCRIBE round trip completes on the server side.
        deadline = time.time() + 5.0
        delivered = False
        while time.time() < deadline:
            valkey_client.publish("events", "payload")
            time.sleep(0.05)
            message = valkey_client.try_get_pubsub_message()
            if message is not None:
                delivered = True
                break

        valkey_client.unsubscribe("events")
        assert delivered is True

    def test_publish_without_subscribers(self, valkey_client):
        # PUBLISH to an unsubscribed channel succeeds and reports zero.
        assert valkey_client.publish("nobody-listening", "hello") == 0


class TestRealValkeyCacheLayer:
    """The cache/rate-limit helpers that sit on top of the client."""

    def test_cache_set_get_delete(self, valkey_client):
        from src.core.database.cache import cache_delete, cache_get, cache_set

        assert cache_set("cache-key", {"data": "value"}) is True
        assert cache_get("cache-key") == {"data": "value"}
        assert cache_delete("cache-key") is True
        assert cache_get("cache-key") is None

    def test_rate_limit_counts_against_the_shared_counter(self, valkey_client):
        from src.core.database.cache import check_rate_limit, reset_rate_limit

        reset_rate_limit("docker:test")

        for expected_remaining in (4, 3, 2, 1, 0):
            allowed, remaining = check_rate_limit(
                "docker:test", limit=5, window_seconds=60
            )
            assert allowed is True
            assert remaining == expected_remaining

        allowed, remaining = check_rate_limit("docker:test", limit=5, window_seconds=60)
        assert allowed is False
        assert remaining == 0

        assert reset_rate_limit("docker:test") is True
        allowed, _ = check_rate_limit("docker:test", limit=5, window_seconds=60)
        assert allowed is True

    def test_rate_limit_window_is_set_on_first_hit(self, valkey_client):
        from src.core.database.cache import check_rate_limit, reset_rate_limit

        reset_rate_limit("docker:window")
        check_rate_limit("docker:window", limit=5, window_seconds=45)

        # The Lua script sets the TTL on the first INCR; without it the
        # counter would persist and rate-limit the caller forever.
        ttl = valkey_client.ttl("ratelimit:docker:window")
        assert 0 < ttl <= 45

    def test_presence_and_session_helpers(self, valkey_client):
        from src.core.database.cache import (
            cache_presence,
            cache_session,
            get_bulk_presence,
            get_cached_presence,
            get_cached_session,
        )

        assert cache_presence(1, "online", "Playing games") is True
        presence = get_cached_presence(1)
        assert presence is not None
        assert presence["status"] == "online"

        cache_presence(2, "idle")
        bulk = get_bulk_presence([1, 2, 999])
        assert set(bulk) == {1, 2}

        assert cache_session("sess-1", user_id=7, data={"ip": "127.0.0.1"}) is True
        session = get_cached_session("sess-1")
        assert session is not None
        assert session["user_id"] == 7


class TestRealValkeyAsync:
    """The event-loop (``glide``) handle against a real server."""

    @pytest.mark.asyncio
    async def test_async_set_get_delete(self, async_valkey_client):
        assert await async_valkey_client.aset("akey", "avalue") is True
        assert await async_valkey_client.aget("akey") == "avalue"
        assert await async_valkey_client.aexists("akey") is True
        assert await async_valkey_client.adelete("akey") == 1
        assert await async_valkey_client.aget("akey") is None

    @pytest.mark.asyncio
    async def test_async_binary_round_trip(self, async_valkey_client):
        payload = bytes(range(256))
        assert await async_valkey_client.aset_bin("ablob", payload) is True
        assert await async_valkey_client.aget_bin("ablob") == payload

    @pytest.mark.asyncio
    async def test_async_structures(self, async_valkey_client):
        await async_valkey_client.ahset("ah", "f", "v")
        assert await async_valkey_client.ahget("ah", "f") == "v"

        await async_valkey_client.arpush("al", "a", "b")
        assert await async_valkey_client.alrange("al", 0, -1) == ["a", "b"]

        await async_valkey_client.asadd("as", "x")
        assert await async_valkey_client.asismember("as", "x") is True

        assert await async_valkey_client.aincr("ac") == 1

        await async_valkey_client.aset_json("aj", {"k": "v"})
        assert await async_valkey_client.aget_json("aj") == {"k": "v"}

    @pytest.mark.asyncio
    async def test_async_lock_yields_while_contended(self, async_valkey_client):
        token = await async_valkey_client.aacquire_lock("alock", timeout=1.0)
        assert token is not None
        assert await async_valkey_client.aacquire_lock("alock", timeout=0.2) is None
        assert await async_valkey_client.arelease_lock("alock", token) is True

    @pytest.mark.asyncio
    async def test_async_lock_does_not_block_the_loop(self, async_valkey_client):
        await async_valkey_client.aacquire_lock("loop-lock", timeout=1.0)

        progressed = False

        async def _other_work() -> None:
            nonlocal progressed
            progressed = True

        task = asyncio.ensure_future(_other_work())
        assert await async_valkey_client.aacquire_lock("loop-lock", timeout=0.3) is None
        await task
        assert progressed is True

    @pytest.mark.asyncio
    async def test_async_scan_and_flush(self, async_valkey_client):
        await async_valkey_client.aset("scan:1", "a")
        await async_valkey_client.aset("scan:2", "b")

        assert sorted(await async_valkey_client.akeys("scan:*")) == ["scan:1", "scan:2"]
        assert await async_valkey_client.aflush_prefix() == 2
        assert await async_valkey_client.akeys("*") == []

    @pytest.mark.asyncio
    async def test_async_health(self, async_valkey_client):
        assert await async_valkey_client.aping() is True
        health = await async_valkey_client.ahealth_check()
        assert health["connected"] is True
        assert health["responsive"] is True


class TestRealValkeyRateLimitStorage:
    """The distributed token-bucket limiter, which is pure Lua + EVAL."""

    def test_token_bucket_allows_then_denies(self, valkey_client):
        from src.core.ratelimit.storage.valkey import ValkeyStorage

        storage = ValkeyStorage()
        storage.clear_all()

        # Capacity 5, negligible refill: the first five cost-1 calls pass.
        for _ in range(5):
            allowed, remaining, _reset = storage.eval_token_bucket(
                "bucket", capacity=5, refill_rate=0.0001, cost=1
            )
            assert allowed is True

        allowed, remaining, reset_after = storage.eval_token_bucket(
            "bucket", capacity=5, refill_rate=0.0001, cost=1
        )
        assert allowed is False
        assert remaining == 0
        assert reset_after > 0

    def test_token_bucket_bucket_survives_between_calls(self, valkey_client):
        from src.core.ratelimit.storage.valkey import ValkeyStorage

        storage = ValkeyStorage()
        storage.clear_all()

        storage.eval_token_bucket("persist", capacity=10, refill_rate=0.0, cost=4)
        _allowed, remaining, _reset = storage.eval_token_bucket(
            "persist", capacity=10, refill_rate=0.0, cost=4
        )
        # 10 - 4 - 4 = 2: the state round-tripped through the server.
        assert remaining == 2

    def test_bucket_mutations_round_trip(self, valkey_client):
        """The locking helpers read-modify-write JSON through the server."""
        from src.core.ratelimit.storage.valkey import ValkeyStorage

        storage = ValkeyStorage()
        storage.clear_all()

        assert storage.increment("counters", "hits") == 1
        assert storage.increment("counters", "hits", 4) == 5
        assert storage.get_bucket("counters")["hits"] == 5

        assert storage.get_and_set("counters", "hits", 0) == 5
        assert storage.get_bucket("counters")["hits"] == 0

        assert storage.add_to_list("events", "recent", 100, max_size=3) == 1
        assert storage.add_to_list("events", "recent", 200, max_size=3) == 2
        assert storage.trim_list("events", "recent", 150) == 1
        assert storage.get_bucket("events")["recent"] == [200]

    def test_locks_are_actually_persisted(self, valkey_client):
        """The storage-level locks wrap the client's distributed lock."""
        from src.core.ratelimit.storage.valkey import ValkeyStorage

        storage = ValkeyStorage()
        token = storage.acquire_lock("thing", timeout=1.0)
        assert token is not None
        try:
            # A second holder is refused while the first holds it.
            assert storage.acquire_lock("thing", timeout=0.2) is None
        finally:
            storage.release_lock("thing", token)
        assert storage.acquire_lock("thing", timeout=0.5) is not None

    def test_delete_by_prefix_only_removes_its_own_buckets(self, valkey_client):
        from src.core.ratelimit.storage.valkey import ValkeyStorage

        storage = ValkeyStorage()
        storage.set_bucket("a", {"n": 1})
        storage.set_bucket("b", {"n": 2})
        valkey_client.set("unrelated", "keep")

        assert storage.delete_by_prefix("") == 2
        assert valkey_client.get("unrelated") == "keep"

    @pytest.mark.asyncio
    async def test_async_token_bucket_allows(self, async_valkey_client):
        from src.core.ratelimit.storage.valkey import ValkeyStorage

        # The async fixture opens only the event-loop handle, so the
        # blocking ``clear_all`` would have nothing to talk to.
        storage = ValkeyStorage()
        await storage.aclear_all()

        allowed, remaining, _reset = await storage.aeval_token_bucket(
            "abucket", capacity=3, refill_rate=0.0001, cost=1
        )
        assert allowed is True
        assert remaining == 2
