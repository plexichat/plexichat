"""HTTP sweep: read surfaces must serialize snowflake ids as decimal strings.

Covers the read endpoints that are not reached by the OpenAPI schema walk or
the earlier auth/artifact sweeps: message/user/server search, server sticker
packs (and their stickers), and custom emojis (list/detail/counts). Each call
is scanned recursively for any integer at or above ``2**53`` -- the point past
which a JavaScript ``Number`` can no longer hold the value exactly -- and for
any ``id`` / ``*_id`` field that came back as a number instead of a string.
"""

import struct
import uuid
import zlib
from typing import Any

MIN_SNOWFLAKE = 2**53

# Field names that are snowflake ids when present in a response payload.
_ID_KEYS = ("id", "user_id", "server_id", "channel_id", "message_id", "author_id")


def _png() -> bytes:
    """Return a minimal valid 1x1 PNG accepted by the emoji/sticker validators."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    raw = b"\x00\x00\x00\x00\x00"
    compressed = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
    idat = (
        struct.pack(">I", len(compressed))
        + b"IDAT"
        + compressed
        + struct.pack(">I", idat_crc)
    )
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return sig + ihdr + idat + iend


def _scan(node: Any, path: str = "$") -> list[str]:
    """Return ``key path = value`` for numeric snowflakes and numeric id fields."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and abs(value) >= MIN_SNOWFLAKE:
                found.append(f"{child} = {value} (numeric snowflake)")
            elif key in _ID_KEYS and isinstance(value, int):
                found.append(f"{child} = {value} (id is not a string)")
            found += _scan(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _scan(value, f"{path}[{index}]")
    return found


def test_scanner_flags_numeric_ids():
    assert _scan({"id": 9007199254740993}) != []
    assert _scan({"id": "9007199254740993"}) == []
    assert _scan({"server_id": 42}) != []  # id-shaped field must be a string
    assert _scan({"total": 42}) == []  # ordinary count is fine
    assert _scan({"nested": [{"user_id": 10**18}]}) != []


def _check(r, where: str) -> Any:
    assert r.status_code == 200, f"{where} -> {r.status_code}: {r.text}"
    body = r.json()
    hits = _scan(body)
    assert not hits, (
        f"{where} returned a numeric snowflake id; serialize it as a decimal "
        "string instead:\n" + "\n".join(hits)
    )
    return body


class TestSearchResponseIds:
    def test_search_endpoints_return_string_ids(
        self, test_client, auth_headers, test_user_with_token, second_test_user, modules
    ):
        owner = test_user_with_token["user"]
        other = second_test_user["user"]

        server = modules.servers.create_server(
            owner.id, f"Search {uuid.uuid4().hex[:6]}"
        )
        dm = modules.messaging.create_dm(owner.id, other.id)
        msg = modules.messaging.send_message(owner.id, dm.id, "hello snowflake search")
        modules.search.index_message(
            msg.id,
            msg.content,
            {
                "author_id": owner.id,
                "conversation_id": dm.id,
                "created_at": msg.created_at,
            },
        )

        body = _check(
            test_client.get(
                "/api/v1/search/messages",
                params={"query": "hello snowflake search"},
                headers=auth_headers,
            ),
            "GET /search/messages",
        )
        assert body["results"], "search returned no messages to inspect"
        for result in body["results"]:
            assert isinstance(result["id"], str)

        _check(
            test_client.get(
                "/api/v1/search/users",
                params={"query": owner.username},
                headers=auth_headers,
            ),
            "GET /search/users",
        )
        _check(
            test_client.get(
                "/api/v1/search/servers",
                params={"query": server.name},
                headers=auth_headers,
            ),
            "GET /search/servers",
        )


class TestStickerResponseIds:
    def test_sticker_endpoints_return_string_ids(
        self, test_client, auth_headers, test_user_with_token, modules
    ):
        owner = test_user_with_token["user"]
        server = modules.servers.create_server(
            owner.id, f"Stickers {uuid.uuid4().hex[:6]}"
        )

        created = _check(
            test_client.post(
                f"/api/v1/servers/{server.id}/stickers",
                headers=auth_headers,
                data={"name": "party-sticker", "tags": "party"},
                files={"image": ("s.png", _png(), "image/png")},
            ),
            "POST /servers/{id}/stickers",
        )
        assert isinstance(created["id"], str)
        assert isinstance(created["pack_id"], str) if created.get("pack_id") else True

        body = _check(
            test_client.get(
                f"/api/v1/servers/{server.id}/stickers", headers=auth_headers
            ),
            "GET /servers/{id}/stickers",
        )
        assert body, "server sticker list was empty"

        _check(
            test_client.get(
                "/api/v1/stickers/suggestions",
                params={"content": "party time"},
                headers=auth_headers,
            ),
            "GET /stickers/suggestions",
        )


class TestEmojiResponseIds:
    def test_emoji_endpoints_return_string_ids(
        self, test_client, auth_headers, test_user_with_token, modules
    ):
        owner = test_user_with_token["user"]
        server = modules.servers.create_server(
            owner.id, f"Emojis {uuid.uuid4().hex[:6]}"
        )

        # The shared test ReactionManager is built without a servers module,
        # unlike production (src/server/initializer.py wires one), so its
        # fail-closed server.manage gate denies every write. Wire it here so
        # the emoji write path can be exercised end to end.
        modules.reactions._servers = modules.servers

        created = _check(
            test_client.post(
                f"/api/v1/servers/{server.id}/emojis",
                headers=auth_headers,
                data={"name": "party_emoji"},
                files={"image": ("e.png", _png(), "image/png")},
            ),
            "POST /servers/{id}/emojis",
        )
        assert isinstance(created["id"], str)

        body = _check(
            test_client.get(
                f"/api/v1/servers/{server.id}/emojis", headers=auth_headers
            ),
            "GET /servers/{id}/emojis",
        )
        assert body, "server emoji list was empty"

        _check(
            test_client.get(
                f"/api/v1/servers/{server.id}/emojis/counts", headers=auth_headers
            ),
            "GET /servers/{id}/emojis/counts",
        )
        _check(
            test_client.get(
                f"/api/v1/servers/{server.id}/emojis/{created['id']}",
                headers=auth_headers,
            ),
            "GET /servers/{id}/emojis/{emoji_id}",
        )
