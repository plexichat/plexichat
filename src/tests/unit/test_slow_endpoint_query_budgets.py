"""Query-count guards for the three slowest read endpoints.

The API layer already memoizes whole responses for pins/search, so wall-clock
probes hide the cold-path cost. These tests pin the *cold* query fan-out with
counting fakes instead: every enrichment lookup that scales with the number of
messages must be a single batched call, and the invite-name lookup must not
repeat its expensive server read on every request.

No core module is mocked beyond the modules the routes already fetch through
``src.api``; the authorization/validation checks stay on the real code path.
"""

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.channels.invites import (
    ChannelInvitesMixin,
    get_invite_metadata_cache,
)
from src.api.routes.messages.messages_list import get_pinned_messages
from src.api.routes.messages.search import search_messages
from src.core.auth.models import AccountType, TokenInfo
from src.core.messaging.schema import SCHEMA_STATEMENTS

pytestmark = [pytest.mark.unit]


def _user(user_id: int = 42) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


def _attachment(index: int):
    return SimpleNamespace(
        id=9000 + index,
        filename="file.bin",
        url=f"/api/v1/media/attachments/stored-{index}.bin",
        content_type="application/octet-stream",
        size=1,
        # A JSON *string* used to defeat the bulk file-id hydration.
        metadata='{"kind": "attachment"}',
    )


def _message(index: int, author_id: int = 7):
    return SimpleNamespace(
        id=1000 + index,
        author_id=author_id,
        content="searchable content",
        created_at=1_700_000_000_000,
        attachments=[_attachment(index)],
        embeds=[],
        metadata=None,
        pinned=False,
    )


class _CountingDB:
    def __init__(self):
        self.calls = []

    def fetch_all(self, sql, params=None):
        self.calls.append((sql, params))
        return []


class _Embeds:
    """Batch-capable embeds module that fails loudly on a per-id fallback."""

    def __init__(self):
        self.db = _CountingDB()

    def _get_manager(self):
        return self

    @property
    def _db(self):
        return self.db

    def are_embeds_suppressed(self, message_id):
        raise AssertionError(f"per-message embed lookup for {message_id}")


class _Media:
    def __init__(self):
        self.bulk_calls = []
        self.per_file_calls = []

    def get_files_by_filenames(self, filenames):
        self.bulk_calls.append(list(filenames))
        return {
            name: SimpleNamespace(id=5000 + i)
            for i, name in enumerate(sorted(filenames))
        }

    def get_file_by_filename(self, filename):
        self.per_file_calls.append(filename)
        raise AssertionError(f"per-attachment media lookup for {filename}")

    def sign_url(self, file_id, user_id, url):
        return SimpleNamespace(url=f"signed:{url}")


class _Auth:
    def __init__(self):
        self.bulk_calls = []

    def get_user_profiles_bulk(self, user_ids):
        self.bulk_calls.append(list(user_ids))
        return {
            str(uid): {
                "username": f"user-{uid}",
                "avatar_url": None,
                "badges": [],
            }
            for uid in user_ids
        }


class _Messaging:
    def __init__(self, messages):
        self.messages = messages
        self.search_calls = 0
        self.pin_calls = 0

    def is_participant(self, conversation_id, user_id):
        # The live gate on the search path; the fakes model an allowed viewer.
        return True

    def search_messages(self, **kwargs):
        self.search_calls += 1
        return list(self.messages)

    def get_pinned_messages(self, user_id, conversation_id):
        self.pin_calls += 1
        return list(self.messages)


class _Servers:
    def get_channel(self, channel_id, user_id):
        return SimpleNamespace(conversation_id=777, id=channel_id, server_id=555)

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        # The live messages.read gate on the search path; allowed viewer.
        return True


def _cold(func):
    """The handler with its cache decorator peeled off, when there is one.

    These tests count the cold-path query fan-out, so a warm response cache
    must not hide it. The read routes no longer install a cache decorator, in
    which case the handler itself is the cold path.
    """
    return getattr(func, "__wrapped__", func)


async def _run_search(messages):
    """Call the undecorated handler so cache hits cannot hide cold queries."""
    auth, media, embeds, messaging = _Auth(), _Media(), _Embeds(), _Messaging(messages)
    with (
        patch.object(api, "get_auth", return_value=auth),
        patch.object(api, "get_media", return_value=media),
        patch.object(api, "get_embeds", return_value=embeds),
        patch.object(api, "get_messaging", return_value=messaging),
        patch.object(api, "get_servers", return_value=_Servers()),
        patch.object(api, "get_db", return_value=None),
    ):
        result = await _cold(search_messages)(
            "12345", content="content", limit=100, current_user=_user()
        )
    return result, auth, media, embeds, messaging


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 50])
async def test_search_enrichment_is_batched_independent_of_hit_count(count):
    messages = [_message(i) for i in range(count)]

    result, auth, media, embeds, messaging = await _run_search(messages)

    assert len(result) == count
    # One author query, one attachment query, one embed-suppression query.
    assert len(auth.bulk_calls) == 1
    assert len(media.bulk_calls) == 1
    assert len(embeds.db.calls) == 1
    assert "GROUP BY message_id" in embeds.db.calls[0][0]
    # The per-item fallbacks were never reached.
    assert media.per_file_calls == []
    assert messaging.search_calls == 1


@pytest.mark.asyncio
async def test_search_preserves_channel_validation_and_not_found():
    with (
        patch.object(api, "get_messaging", return_value=None),
        patch.object(api, "get_servers", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await _cold(search_messages)(
                "not-a-snowflake", content="x", limit=10, current_user=_user()
            )
    assert exc.value.status_code == 400


async def _run_pins(messages):
    auth, media, embeds, messaging = _Auth(), _Media(), _Embeds(), _Messaging(messages)
    with (
        patch.object(api, "get_auth", return_value=auth),
        patch.object(api, "get_media", return_value=media),
        patch.object(api, "get_embeds", return_value=embeds),
        patch.object(api, "get_messaging", return_value=messaging),
        patch.object(api, "get_servers", return_value=_Servers()),
        patch.object(api, "get_db", return_value=None),
    ):
        result = await _cold(get_pinned_messages)(
            "12345", limit=100, current_user=_user()
        )
    return result, auth, media, embeds, messaging


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 50])
async def test_pins_enrichment_is_batched_independent_of_pin_count(count):
    messages = [_message(i) for i in range(count)]

    result, auth, media, embeds, messaging = await _run_pins(messages)

    assert len(result) == count
    assert len(auth.bulk_calls) == 1
    assert len(media.bulk_calls) == 1
    assert len(embeds.db.calls) == 1
    assert media.per_file_calls == []
    assert messaging.pin_calls == 1


@pytest.mark.asyncio
async def test_pins_keep_empty_channel_empty_and_validate_id():
    with (
        patch.object(api, "get_messaging", return_value=None),
        patch.object(api, "get_servers", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await _cold(get_pinned_messages)("-1", limit=10, current_user=_user())
    assert exc.value.status_code == 400


class _CountingInviteServers:
    def __init__(self):
        self.get_invite_calls = 0
        self.get_server_calls = 0
        self.invite = SimpleNamespace(
            code="CODE1234",
            server_id=555,
            channel_id=777,
            inviter_id=888,
            uses=1,
            max_uses=5,
            expires_at=None,
        )

    def get_invite(self, code):
        self.get_invite_calls += 1
        return self.invite if code == "CODE1234" else None

    def get_server(self, server_id, user_id):
        self.get_server_calls += 1
        return SimpleNamespace(name="cached server")

    def get_member(self, server_id, user_id):
        # Mirror the production facade: a warm invite-name hit is re-gated on
        # a cheap, live membership probe.
        return SimpleNamespace(id=user_id)


@pytest.mark.asyncio
async def test_invite_info_caches_server_name_but_not_live_invite_fields():
    get_invite_metadata_cache().clear()
    servers = _CountingInviteServers()
    user = _user()

    with patch.object(api, "get_servers", return_value=servers):
        first = await ChannelInvitesMixin()._get_invite_info("CODE1234", user)
        # The invite row is live data: it must be re-read on every request.
        servers.invite.uses = 2
        second = await ChannelInvitesMixin()._get_invite_info("CODE1234", user)

    assert first.server_name == second.server_name == "cached server"
    assert int(first.uses) == 1
    assert int(second.uses) == 2
    assert servers.get_invite_calls == 2
    # The heavy server read happened only on the cold miss.
    assert servers.get_server_calls == 1
    get_invite_metadata_cache().clear()


@pytest.mark.asyncio
async def test_invite_name_cache_is_keyed_by_viewer_and_never_stores_denials():
    get_invite_metadata_cache().clear()
    servers = _CountingInviteServers()

    class _NoAccess(_CountingInviteServers):
        def get_server(self, server_id, user_id):  # pyright: ignore[reportIncompatibleMethodOverride]
            self.get_server_calls += 1
            return None

    denied = _NoAccess()
    with patch.object(api, "get_servers", return_value=denied):
        first = await ChannelInvitesMixin()._get_invite_info("CODE1234", _user(1))
        second = await ChannelInvitesMixin()._get_invite_info("CODE1234", _user(1))
    assert first.server_name is None and second.server_name is None
    assert denied.get_server_calls == 2  # a denial is never memoized

    with patch.object(api, "get_servers", return_value=servers):
        allowed = await ChannelInvitesMixin()._get_invite_info("CODE1234", _user(2))
        again = await ChannelInvitesMixin()._get_invite_info("CODE1234", _user(2))
    assert allowed.server_name == again.server_name == "cached server"
    assert servers.get_server_calls == 1  # different viewer has its own entry
    get_invite_metadata_cache().clear()


@pytest.mark.asyncio
async def test_invite_info_validation_still_runs_before_any_lookup():
    get_invite_metadata_cache().clear()
    servers = _CountingInviteServers()

    with patch.object(api, "get_servers", return_value=servers):
        with pytest.raises(HTTPException) as exc:
            await ChannelInvitesMixin()._get_invite_info("bad code!!", _user())
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            await ChannelInvitesMixin()._get_invite_info("MISSING1", _user())
        assert exc.value.status_code == 404

    assert servers.get_invite_calls == 1  # only the valid-code lookup ran
    assert servers.get_server_calls == 0


# --- index-backed storage queries -----------------------------------------
# The route can only be as cheap as the queries it triggers. Pin/search reads
# are driven by a conversation_id equality filter, so the real messaging schema
# must keep them on an index rather than a full msg_messages / msg_pinned scan.

_PIN_QUERY = (
    "SELECT m.*, p.pinned_by, p.pinned_at FROM msg_messages m "
    "INNER JOIN msg_pinned p ON m.id = p.message_id "
    "WHERE p.conversation_id = ? AND m.deleted = 0 ORDER BY p.pinned_at DESC"
)
_SEARCH_QUERY = (
    "SELECT m.* FROM msg_messages m "
    "WHERE m.conversation_id = ? AND m.deleted = 0 "
    "AND (m.content LIKE ? ESCAPE '\\') ORDER BY m.id DESC LIMIT ?"
)


def _query_plan(sql, params):
    conn = sqlite3.connect(":memory:")
    try:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        return "\n".join(
            row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)
        )
    finally:
        conn.close()


def test_pins_query_uses_the_pinned_conversation_index():
    plan = _query_plan(_PIN_QUERY, (123,))
    assert "idx_msg_pinned_conv" in plan, plan
    assert "SCAN m" not in plan, plan


def test_search_query_uses_the_conversation_index():
    plan = _query_plan(_SEARCH_QUERY, (123, "%needle%", 25))
    assert "idx_msg_messages_conv" in plan, plan
    assert "SCAN m" not in plan, plan
