"""Unit ratchet: the snowflake-id oracle sees snake_case and camelCase keys.

``src.core.events.snowflakes.is_id_field`` is the single source of truth
every id gate consults (gateway builders, the wire backstop, the OpenAPI
walk, webhook/interaction ratchets). Client-facing projections also serve
camelCase id keys (the suite launcher's ``channelId``/``serverId``/
``conversationId``/``resourceId``), so a snake_case-only oracle would let a
numeric camelCase id slip past every gate at once.

These tests pin both spellings — and the near-misses (``valid``/``grid``
end in lowercase ``id`` and must NOT match) — so the rule cannot silently
narrow again.
"""

from src.core.events.snowflakes import is_id_field, stringify_snowflake_ids

BIG = 9007199254740993  # past 2**53: a JSON number would lose precision


def test_snake_case_id_fields_match():
    assert is_id_field("id")
    assert is_id_field("user_id")
    assert is_id_field("channel_id")
    assert is_id_field("recipient_ids")


def test_camel_case_id_fields_match():
    assert is_id_field("userId")
    assert is_id_field("channelId")
    assert is_id_field("serverId")
    assert is_id_field("conversationId")
    assert is_id_field("resourceId")
    assert is_id_field("recipientIds")
    assert is_id_field("appId")


def test_lookalike_keys_do_not_match():
    assert not is_id_field("valid")
    assert not is_id_field("invalid")
    assert not is_id_field("grid")
    assert not is_id_field("pyramid")
    assert not is_id_field("username")
    assert not is_id_field("timestamp")
    assert not is_id_field("flags")
    assert not is_id_field("updatedAt")
    assert not is_id_field("")


def test_stringify_converts_camel_case_ids():
    payload = {
        "channelId": BIG,
        "serverId": BIG + 1,
        "recipientIds": [BIG + 2, None, BIG + 3],
        "valid": True,
        "timestamp": 1760000000000,
    }
    out = stringify_snowflake_ids(payload)
    assert out["channelId"] == str(BIG)
    assert out["serverId"] == str(BIG + 1)
    assert out["recipientIds"] == [str(BIG + 2), None, str(BIG + 3)]
    # Non-id values are preserved.
    assert out["valid"] is True
    assert out["timestamp"] == 1760000000000
    # The caller's object is never mutated.
    assert payload["channelId"] == BIG
    assert payload["recipientIds"][0] == BIG + 2
    # Stringifying twice is a fixed point.
    assert stringify_snowflake_ids(out) == out


def test_launcher_projection_is_a_fixed_point():
    """The suite launcher shape stays compliant through the backstop."""
    item = {
        "id": str(7),
        "appId": "plexistash",
        "scope": {"serverId": str(BIG)},
        "createdAt": 1760000000000,
    }
    assert stringify_snowflake_ids(item) == item
