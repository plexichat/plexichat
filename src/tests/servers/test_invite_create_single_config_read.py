"""Invite creation reads the ``invites`` config once, not twice per request.

``MemberInviteMixin.create_invite`` used to call ``_invite_config()`` inside
``_enforce_invite_quota`` and then again in ``create_invite`` itself to pick up
the active-invite caps. Each call performed a fresh ``config.get("invites")``
and re-imported ``utils.config``, so minting a single invite paid for the same
config lookup twice. The quota helper now accepts a config snapshot and its
only caller fetches that snapshot once.
"""

import pytest

import src.core.servers.handlers.members.invites as invites_mod

pytestmark = pytest.mark.unit


def test_create_invite_reads_invites_config_once(server_with_channels, monkeypatch):
    server, owner, _admin, _member, _outsider, general, *_rest = server_with_channels
    servers = _rest[-1]

    real_config = invites_mod._invite_config
    reads = []

    def counting_config():
        reads.append(1)
        return real_config()

    monkeypatch.setattr(invites_mod, "_invite_config", counting_config)
    # Keep the in-process creation window from rejecting this single mint.
    invites_mod._reset_invite_windows()

    invite = servers.create_invite(user_id=owner.id, channel_id=general.id)

    assert invite is not None
    assert len(reads) == 1, (
        f"create_invite read the invites config {len(reads)} times; expected 1"
    )
