"""A recovery-address change must retire OAuth links minted from that address.

``OAuthMixin.oauth_login`` silently links a provider identity to an existing
local account by matching the asserted *email* (see the Microsoft UPN check in
``api/routes/auth/oauth.py``, which documents that this is a takeover primitive).
An attacker who controls the old inbox can therefore plant a durable login
credential with nothing more than an OAuth assertion carrying that address.

``update_user`` already treats an address change as a credential transition: it
retires verification/reset tokens, every session and every trusted device. The
external-account link was left behind, so the old-inbox attacker kept a working
way back into the reclaimed account. These tests pin the fail-closed behaviour:
the link dies with the address, while a non-credential profile edit leaves it
untouched.
"""

import uuid

import pytest

from src.core.auth.models import AuthStatus

pytestmark = pytest.mark.auth


def _register(auth_manager, label):
    suffix = uuid.uuid4().hex[:10]
    email = f"{label}_{suffix}@test.local"
    user = auth_manager.register(
        username=f"{label}_{suffix}", email=email, password="TestPass123!"
    )
    return user, email


def _links(db, user_id):
    return db.fetch_all(
        "SELECT provider, external_id FROM auth_external_accounts WHERE user_id = ?",
        (user_id,),
    )


class TestOAuthLinkRetiredOnEmailChange:
    def test_recovery_email_change_drops_attacker_oauth_link(self, db, auth_manager):
        victim, old_email = _register(auth_manager, "oauth_retire")
        external_id = f"attacker-sub-{uuid.uuid4().hex[:10]}"

        # The attacker controls the old inbox, so an OAuth assertion carrying
        # that address auto-links to the victim (oauth_login matches by email).
        first = auth_manager.oauth_login(
            provider="google",
            external_id=external_id,
            email=old_email,
            email_verified=True,
        )
        assert first.status == AuthStatus.SUCCESS
        assert first.user.id == victim.id
        assert len(_links(db, victim.id)) == 1  # link is live before the switch

        new_email = f"reclaimed_{uuid.uuid4().hex[:10]}@test.local"
        auth_manager.update_user(victim.id, email=new_email)

        # The link was minted from the old address; it must not outlive the
        # transition that was supposed to evict the old-inbox attacker.
        assert _links(db, victim.id) == []

        second = auth_manager.oauth_login(
            provider="google",
            external_id=external_id,
            email=old_email,
            email_verified=True,
        )
        # The assertion can no longer reach the reclaimed account: with no link
        # and no matching local address it only mints a fresh, unrelated user.
        assert second.status == AuthStatus.SUCCESS
        assert second.user.id != victim.id

    def test_username_edit_keeps_oauth_link(self, db, auth_manager):
        """Control: a non-credential profile edit must not drop the link."""
        victim, old_email = _register(auth_manager, "oauth_keep")
        external_id = f"legit-sub-{uuid.uuid4().hex[:10]}"
        first = auth_manager.oauth_login(
            provider="google",
            external_id=external_id,
            email=old_email,
            email_verified=True,
        )
        assert first.status == AuthStatus.SUCCESS
        assert first.user.id == victim.id

        auth_manager.update_user(victim.id, username=f"renamed_{uuid.uuid4().hex[:10]}")

        assert len(_links(db, victim.id)) == 1
        again = auth_manager.oauth_login(
            provider="google",
            external_id=external_id,
            email=old_email,
            email_verified=True,
        )
        assert again.status == AuthStatus.SUCCESS
        assert again.user.id == victim.id
