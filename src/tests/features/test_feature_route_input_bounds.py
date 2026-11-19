"""Boundary validation for feature-route ids and scalar inputs.

Two write routes accepted values the rest of the codebase rejects at the
boundary:

* ``PUT /users/@me/last-chat`` parsed ids with a bare ``int()`` (negatives,
  zero, out-of-range values, Unicode digit homoglyphs and Python forms such as
  ``1_2`` all passed) and persisted an unbounded ``scroll_position`` straight
  into SQLite/Valkey.
* ``DELETE /push/tokens/id/{token_id}`` parsed the row id with a bare
  ``int()``, so an oversized value reached an ``OverflowError`` in the DELETE
  and degraded to a 500.

Both routes now use the shared snowflake guard and a bounded scroll offset.
These tests pin that contract without needing a live database.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

USER = SimpleNamespace(user_id=123, token_type="user")


# ---------------------------------------------------------------------------
# last-chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_chat_rejects_unbounded_and_negative_scroll_position():
    import src.api.routes.features.last_chat as mod

    for bad in (-1, mod.MAX_SCROLL_POSITION + 1, 10**30):
        with pytest.raises(ValidationError):
            mod.LastChatRequest(
                conversation_id="1",
                last_message_id="2",
                scroll_position=bad,
            )

    # A legitimate offset is still accepted.
    ok = mod.LastChatRequest(
        conversation_id="1", last_message_id="2", scroll_position=0
    )
    assert ok.scroll_position == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["-5", "0", "1_2", "+12", "١٢٣", "9" * 25])
async def test_last_chat_rejects_loose_id_forms(monkeypatch, bad_id):
    import src.api.routes.features.last_chat as mod

    class _NoWriteService:
        def __init__(self, db, participant_svc=None):
            raise AssertionError("service must not be constructed for a bad id")

    monkeypatch.setattr(mod, "LastChatService", _NoWriteService)

    body = mod.LastChatRequest(conversation_id=bad_id, last_message_id="2")
    with pytest.raises(HTTPException) as exc:
        await mod.save_last_chat(body, current_user=USER)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_last_chat_accepts_valid_snowflake_ids(monkeypatch):
    import src.api.routes.features.last_chat as mod

    seen = {}

    class _Service:
        def __init__(self, db, participant_svc=None):
            pass

        def save_last_chat(self, user_id, conversation_id, last_message_id, scroll_position=0):
            seen.update(
                conversation_id=conversation_id,
                last_message_id=last_message_id,
                scroll_position=scroll_position,
            )
            return {"conversation_id": conversation_id}

    monkeypatch.setattr(mod, "LastChatService", _Service)
    monkeypatch.setattr(mod.api, "get_db", lambda: object())
    monkeypatch.setattr(mod.api, "get_messaging", lambda: None)

    body = mod.LastChatRequest(
        conversation_id="9007199254740993", last_message_id="9007199254740995"
    )
    await mod.save_last_chat(body, current_user=USER)
    assert seen == {
        "conversation_id": 9007199254740993,
        "last_message_id": 9007199254740995,
        "scroll_position": 0,
    }


# ---------------------------------------------------------------------------
# push token id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["-1", "0", "1_2", "+12", "١٢٣", "9" * 25])
async def test_push_token_id_rejects_loose_forms(monkeypatch, bad_id):
    import src.api.routes.features.push as mod

    calls = []

    class _FakeManager:
        def __init__(self, db):
            pass

        def unregister_token_by_id(self, user_id, token_id):
            calls.append((user_id, token_id))
            return True

    monkeypatch.setattr("src.core.push.manager.PushManager", _FakeManager)
    monkeypatch.setattr(mod.api, "get_db", lambda: object())

    with pytest.raises(HTTPException) as exc:
        await mod.unregister_push_token_by_id(bad_id, current_user=USER)
    assert exc.value.status_code == 400
    assert calls == [], "no delete may run for a malformed token id"


@pytest.mark.asyncio
async def test_push_token_id_accepts_valid_snowflake(monkeypatch):
    import src.api.routes.features.push as mod

    calls = []

    class _FakeManager:
        def __init__(self, db):
            pass

        def unregister_token_by_id(self, user_id, token_id):
            calls.append((user_id, token_id))
            return True

    monkeypatch.setattr("src.core.push.manager.PushManager", _FakeManager)
    monkeypatch.setattr(mod.api, "get_db", lambda: object())

    await mod.unregister_push_token_by_id("9007199254740993", current_user=USER)
    assert calls == [(123, 9007199254740993)]
