"""REGRESSION: unwired servers must fail closed in the export view gate.

``_require_export_channel_view`` guards transcript export, which reads and
returns a full conversation transcript. A ``srv_channels.conversation_id``
alias can back the target conversation in the DB while the servers module is
unwired. The gate used to ``return`` (allow) on a falsy ``servers_mod``, so a
member denied ``channels.view`` could export a private channel's transcript by
passing the linked conversation id as the URL channel id.

Invariant: a check that cannot be evaluated because its dependency is unwired
must DENY, not skip. A wired store with no backing channel still allows a
genuine DM/group.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_export_view_gate.py
"""

import pytest
from fastapi import HTTPException

from src.api.routes.messages.export import _require_export_channel_view

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
CONV = 6000
CHANNEL = 7000


class _NoBackingChannel:
    """Wired store: the conversation is a genuine DM/group."""

    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredAliasNoGrant:
    """Wired store: alias resolves to a channel but get_channel is missing."""

    def channel_exists(self, channel_id):
        return False

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}


def test_unwired_denies_export():
    with pytest.raises(HTTPException) as ei:
        _require_export_channel_view(None, CONV, USER)
    assert ei.value.status_code == 404


def test_wired_genuine_dm_still_allowed():
    _require_export_channel_view(_NoBackingChannel(), CONV, USER)  # no raise


def test_wired_alias_unresolvable_grant_denies():
    with pytest.raises(HTTPException) as ei:
        _require_export_channel_view(_WiredAliasNoGrant(), CONV, USER)
    assert ei.value.status_code == 404
