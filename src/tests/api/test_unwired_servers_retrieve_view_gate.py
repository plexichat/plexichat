"""REGRESSION: unwired servers must fail closed in the message-retrieval view gate.

``_require_channel_view`` in ``messages_crud/retrieve.py`` guards the get/delete
message routes. Participation is seeded for every server member regardless of
``channels.view``, and a ``srv_channels.conversation_id`` alias can back the
URL id in the DB while the servers module is unwired. The gate used to
``return`` (allow) on a falsy ``servers_mod``, so a member denied view could
fetch a private message by id -- including its attachments and viewer-bound
signed URLs.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_retrieve_view_gate.py
"""

import pytest
from fastapi import HTTPException

from src.api.routes.messages.messages_crud.retrieve import _require_channel_view

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
CONV = 6000
CHANNEL = 7000


class _NoBackingChannel:
    """Wired store: no server-channel signal at all -> genuine DM/group."""

    pass


class _WiredAliasNoGrant:
    """Wired store: alias resolves to a channel but get_channel is missing."""

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}


def test_unwired_denies_retrieve():
    with pytest.raises(HTTPException) as ei:
        _require_channel_view(CONV, USER, None)
    assert ei.value.status_code == 404


def test_wired_genuine_dm_still_allowed():
    _require_channel_view(CONV, USER, _NoBackingChannel())  # no raise


def test_wired_alias_unresolvable_grant_denies():
    with pytest.raises(HTTPException) as ei:
        _require_channel_view(CONV, USER, _WiredAliasNoGrant())
    assert ei.value.status_code == 404
