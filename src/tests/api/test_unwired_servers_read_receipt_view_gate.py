"""REGRESSION: unwired servers must fail closed in the read-receipt view gate.

``_require_read_receipt_channel_view`` guards unread counts and read-state
lookups. A member denied ``channels.view`` can otherwise pass a private
channel's linked ``conversation_id`` as the URL alias and reach the
participation-only read-state lookup (server membership seeds participation).
The gate used to ``return`` (allow) on a falsy ``servers_mod``.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_read_receipt_view_gate.py
"""

import pytest
from fastapi import HTTPException

from src.api.routes.messages.read_receipts import _require_read_receipt_channel_view

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
CONV = 6000
CHANNEL = 7000


class _NoBackingChannel:
    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredAliasNoGrant:
    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}

    # no get_channel / channel_exists -> cannot prove the view grant


def test_unwired_denies_read_receipt():
    with pytest.raises(HTTPException) as ei:
        _require_read_receipt_channel_view(CONV, USER, None)
    assert ei.value.status_code == 404


def test_wired_genuine_dm_still_allowed():
    _require_read_receipt_channel_view(CONV, USER, _NoBackingChannel())


def test_wired_alias_unresolvable_grant_denies():
    with pytest.raises(HTTPException) as ei:
        _require_read_receipt_channel_view(CONV, USER, _WiredAliasNoGrant())
    assert ei.value.status_code == 404
