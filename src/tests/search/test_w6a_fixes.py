"""
Regression tests for Wave 6 Agent 24 search fixes (W6-A).

Covers:
- F-1  per-result channel visibility re-check for global searches
- F-2  discovery fail-closed without servers module
- F-5  Meilisearch filter-value escaping
- F-10 server/channel metadata populated during indexing
- F-11 reindex_all preserves deleted filtering + author fields
- F-15 PG-compatible suggestions query
- F-17 backend failures surface as errors instead of empty results
- P-1  mentions:me / from:me alias resolution
- P-2  in:#channel leading-hash resolution
"""

import json
import uuid

import pytest

from src.core.search.discovery.manager import DiscoveryManager
from src.core.search.exceptions import (
    SearchBackendError,
    SearchPermissionError,
)
from src.core.search.indexer.meilisearch import MeilisearchIndexer
from src.core.search.indexer.sqlite_fts import SQLiteFTS5Indexer
from src.core.search.models import IndexedMessage, MessageSearchResult


@pytest.mark.search
class TestMeAliasResolution:
    """P-1: from:me / mentions:me must resolve to the caller."""

    def test_resolve_me_alias(self, users_with_dm):
        user1, user2, dm, messages, search = users_with_dm
        proc = search._filter_processor

        assert proc._resolve_user("me", caller_id=user1.id) == user1.id
        assert proc._resolve_user("@me", caller_id=user1.id) == user1.id
        assert proc._resolve_user("ME", caller_id=user1.id) == user1.id

    def test_from_me_filters_to_caller(self, users_with_dm):
        user1, user2, dm, messages, search = users_with_dm

        results = search.search_messages(user1.id, "from:me hello")
        assert all(r.author_id == user1.id for r in results)

    def test_mentions_me_matches_caller(self, users_with_dm):
        user1, user2, dm, messages, search = users_with_dm

        # Index a message that mentions user1.
        search.index_message(
            990001,
            f"ping <@{user1.id}>",
            {"author_id": user2.id, "conversation_id": dm.id},
        )
        results = search.search_messages(user1.id, "mentions:me ping")
        assert any(r.message_id == 990001 for r in results)


@pytest.mark.search
class TestChannelHashResolution:
    """P-2: in:#channel strips the leading hash."""

    def test_resolve_channel_hash_prefix(self, users_with_server):
        owner, member1, member2, server, servers, search = users_with_server
        chan = servers.create_channel(owner.id, server.id, "w6a-general")
        proc = search._filter_processor

        assert proc._resolve_channel("#w6a-general") == chan.id
        assert proc._resolve_channel("w6a-general") == chan.id


@pytest.mark.search
class TestPrivateChannelLeak:
    """F-1: global search re-verifies per-result channel visibility."""

    def _make_private_channel_message(self, modules, owner, member, server):
        servers = modules.servers
        chan = servers.create_channel(
            owner.id, server.id, f"private-{uuid.uuid4().hex[:6]}"
        )
        row = modules._db.fetch_one(
            "SELECT conversation_id FROM srv_channels WHERE id = ?", (chan.id,)
        )
        conv_id = row["conversation_id"]

        # Deny the member channel-level read access via a member override.
        import time

        ts = int(time.time() * 1000)
        modules._db.execute(
            """INSERT INTO srv_channel_overrides
               (channel_id, target_type, target_id, allow, deny, created_at, updated_at)
               VALUES (?, 'member', ?, '{}', ?, ?, ?)""",
            (chan.id, member.id, json.dumps({"messages.read": True}), ts, ts),
        )
        return chan, conv_id

    def test_member_without_channel_read_cannot_search_it(self, modules, user_pool):
        owner = user_pool.get_user()
        member = user_pool.get_user()
        server = modules.servers.create_server(owner.id, f"S-{uuid.uuid4().hex[:6]}")
        modules.servers.add_member(server.id, member.id)

        chan, conv_id = self._make_private_channel_message(
            modules, owner, member, server
        )

        modules.search.index_message(
            990100,
            "secretplan alpha",
            {
                "author_id": owner.id,
                "conversation_id": conv_id,
                "server_id": server.id,
                "channel_id": chan.id,
            },
        )

        # Owner can still find it.
        owner_hits = modules.search.search_messages(owner.id, "secretplan")
        assert any(r.message_id == 990100 for r in owner_hits)

        # Channel-denied member gets nothing from the global search.
        member_hits = modules.search.search_messages(member.id, "secretplan")
        assert all(r.message_id != 990100 for r in member_hits)

    def test_verify_results_visible_drops_inaccessible(self, modules, user_pool):
        owner = user_pool.get_user()
        outsider = user_pool.get_user()
        server = modules.servers.create_server(owner.id, f"S-{uuid.uuid4().hex[:6]}")
        chan = modules.servers.create_channel(
            owner.id, server.id, f"pub-{uuid.uuid4().hex[:6]}"
        )

        forged = MessageSearchResult(
            id=1,
            message_id=1,
            content="leak",
            author_id=owner.id,
            conversation_id=0,
            server_id=server.id,
            channel_id=chan.id,
        )
        mgr = modules.search
        verified = mgr._verify_results_visible(outsider.id, [forged])
        assert verified == []


@pytest.mark.search
class TestDiscoveryFailClosed:
    """F-2: discovery denies mutations when servers module is absent."""

    def test_list_server_denied_without_servers_module(self, db_and_modules):
        db = db_and_modules[0]
        dm = DiscoveryManager(db, None)
        with pytest.raises(SearchPermissionError):
            dm.list_server(1, 123, "gaming")

    def test_unlist_server_denied_without_servers_module(self, db_and_modules):
        db = db_and_modules[0]
        dm = DiscoveryManager(db, None)
        with pytest.raises(SearchPermissionError):
            dm.unlist_server(1, 123)

    def test_bump_permission_check_denied_without_servers_module(self, db_and_modules):
        db = db_and_modules[0]
        dm = DiscoveryManager(db, None)
        with pytest.raises(SearchPermissionError):
            dm._check_bump_permission(1, 123)


@pytest.mark.search
class TestMeilisearchFilterEscaping:
    """F-5: category values are escaped before filter interpolation."""

    def test_escape_filter_value(self):
        assert (
            MeilisearchIndexer._escape_filter_value('ga"ming OR is_public = false')
            == 'ga\\"ming OR is_public = false'
        )
        assert MeilisearchIndexer._escape_filter_value("back\\slash") == "back\\\\slash"

    def test_filter_equality_quotes_value(self):
        out = MeilisearchIndexer._filter_equality("category", 'x" OR is_public = true')
        assert out == 'category = "x\\" OR is_public = true"'

    def test_search_servers_escapes_category(self):
        class RecordingClient:
            def __init__(self):
                self.bodies = []

            def request(self, method, path, body=None):
                if "/search" in path:
                    self.bodies.append(body)
                    return {"hits": []}
                return {}

        client = RecordingClient()
        indexer = MeilisearchIndexer(http_client=client)
        indexer.initialize()
        evil = 'gaming" OR is_public = false OR category = "x'
        indexer.search_servers("query", category=evil)
        assert client.bodies, "search request expected"
        sent = client.bodies[0]["filter"]
        expected = (
            "is_public = true AND category = "
            '"gaming\\" OR is_public = false OR category = \\"x"'
        )
        # The injected quotes are escaped, so the whole value stays one
        # quoted token and cannot alter filter semantics.
        assert sent == expected


@pytest.mark.search
class TestServerChannelMetadataIndexed:
    """F-10: indexed documents carry server_id/channel_id."""

    def test_index_message_resolves_scopes_from_conversation(self, users_with_server):
        owner, member1, member2, server, servers, search = users_with_server
        chan = servers.create_channel(
            owner.id, server.id, f"idx-{uuid.uuid4().hex[:6]}"
        )
        row = search._db.fetch_one(
            "SELECT conversation_id FROM srv_channels WHERE id = ?", (chan.id,)
        )

        search.index_message(
            990200,
            "scoped metadata probe",
            {"author_id": owner.id, "conversation_id": row["conversation_id"]},
        )
        meta = search._db.fetch_one(
            "SELECT server_id, channel_id FROM search_message_index WHERE message_id = 990200"
        )
        assert meta["server_id"] == server.id
        assert meta["channel_id"] == chan.id


@pytest.mark.search
class TestReindexAllPreservesData:
    """F-11: rebuild keeps deleted-flag filtering and author fields."""

    def test_reindex_all_populates_author_and_drops_deleted(self, users_with_dm):
        user1, user2, dm, messages, search = users_with_dm

        # Soft-delete one message directly.
        victim = messages[0]
        search._db.execute(
            "UPDATE msg_messages SET deleted = 1 WHERE id = ?", (victim.id,)
        )

        indexed = search.reindex_all(force=True)
        assert indexed >= 1

        rows = {
            r["message_id"]: r
            for r in search._db.fetch_all("SELECT * FROM search_message_index")
        }
        assert victim.id not in rows, "deleted message resurrected by rebuild"

        live = messages[2]
        assert live.id in rows
        author = search._db.fetch_one(
            "SELECT username FROM auth_users WHERE id = ?", (user1.id,)
        )
        fts_row = search._db.fetch_one(
            "SELECT author_username FROM search_messages_fts WHERE message_id = ?",
            (str(live.id),),
        )
        assert fts_row is not None
        assert fts_row["author_username"] == author["username"]


@pytest.mark.search
class TestSuggestionsQuery:
    """F-15: suggestions query is GROUP BY based (PG compatible)."""

    def test_suggestions_after_history(self, users_with_dm):
        user1, user2, dm, messages, search = users_with_dm
        db = search._db
        for i, q in enumerate(["alpha one", "alpha two", "alpha one"]):
            db.execute(
                """INSERT INTO search_history
                   (id, user_id, query, search_type, result_count, searched_at)
                   VALUES (?, ?, ?, 'message', 1, ?)""",
                (700000 + i, user1.id, q, 1000 + i),
            )
        out = search.get_search_suggestions(user1.id, "alpha", limit=10)
        assert "alpha one" in out
        assert "alpha two" in out


@pytest.mark.search
class TestBackendFailureSurfaces:
    """F-17: backend failures raise instead of returning empty results."""

    def test_sqlite_indexer_raises_on_backend_failure(self, users_with_dm):
        user1, user2, dm, messages, search = users_with_dm
        indexer = search._indexer
        if not isinstance(indexer, SQLiteFTS5Indexer):
            pytest.skip("test targets the SQLite FTS5 indexer")

        class Boom:
            def fetch_all(self, sql, params=None):
                raise RuntimeError("backend down")

        original_db = indexer._db
        indexer._db = Boom()
        try:
            with pytest.raises(SearchBackendError):
                indexer.search_messages("hello")
        finally:
            indexer._db = original_db

    def test_meilisearch_raises_on_backend_failure(self):
        class FailingClient:
            def request(self, method, path, body=None):
                if "/search" in path:
                    raise RuntimeError("connection refused")
                return {}

        indexer = MeilisearchIndexer(http_client=FailingClient())
        indexer.initialize()
        with pytest.raises(SearchBackendError):
            indexer.search_messages("hello")

    def test_indexed_message_document_has_scopes(self):
        doc = IndexedMessage(
            message_id=1,
            content="x",
            author_id=1,
            conversation_id=2,
            server_id=3,
            channel_id=4,
        )
        assert doc.server_id == 3 and doc.channel_id == 4
