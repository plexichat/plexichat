"""Silent OAuth email auto-link must fail closed without a verified, trusted assertion.

``OAuthMixin.oauth_login`` links a provider identity to an existing local
account by matching the asserted email -- the "takeover primitive" called out by
the Microsoft UPN check in ``api/routes/auth/oauth.py`` and by N687's
email-change retirement. The HTTP callback verifies the provider's
``email_verified`` signal before calling the manager, but the linking boundary
itself accepted any caller-supplied ``email`` with no verification flag and no
provider allow-list. Any other caller (mobile gateway, federation bridge,
plugin, future route) could therefore attach an attacker-chosen identity to a
victim simply by echoing the victim's address.

These tests pin the fail-closed policy: no silent link without a verified email
from a trusted provider, and no session minted for the victim.
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


def _sessions(db, user_id):
    return db.fetch_one(
        "SELECT COUNT(*) AS c FROM auth_sessions WHERE user_id = ?", (user_id,)
    )["c"]


class TestOAuthAutoLinkRequiresVerifiedEmail:
    def test_unverified_assertion_cannot_link_to_victim(self, db, auth_manager):
        """A crafted assertion echoing the victim's address must not link."""
        victim, email = _register(auth_manager, "autolink_victim")
        attacker_sub = f"attacker-{uuid.uuid4().hex[:10]}"

        # No email_verified signal: the "provider" never proved it owns this
        # address. This is the takeover the route-level checks are meant to
        # stop; it must also be stopped at the link boundary itself.
        result = auth_manager.oauth_login(
            provider="google", external_id=attacker_sub, email=email
        )

        assert result.status != AuthStatus.SUCCESS or result.user.id != victim.id
        assert _links(db, victim.id) == []
        assert _sessions(db, victim.id) == 0

    def test_untrusted_provider_cannot_link_even_with_verified_flag(
        self, db, auth_manager
    ):
        """Only trusted/configured providers may assert an email for linking."""
        victim, email = _register(auth_manager, "autolink_untrusted")
        attacker_sub = f"rogue-{uuid.uuid4().hex[:10]}"

        result = auth_manager.oauth_login(
            provider="rogue-provider",
            external_id=attacker_sub,
            email=email,
            email_verified=True,
        )

        assert result.status != AuthStatus.SUCCESS or result.user.id != victim.id
        assert _links(db, victim.id) == []
        assert _sessions(db, victim.id) == 0

    def test_verified_trusted_provider_still_links(self, db, auth_manager):
        """Control: a verified assertion from a trusted provider still links."""
        victim, email = _register(auth_manager, "autolink_legit")
        sub = f"legit-{uuid.uuid4().hex[:10]}"

        result = auth_manager.oauth_login(
            provider="google", external_id=sub, email=email, email_verified=True
        )

        assert result.status == AuthStatus.SUCCESS
        assert result.user.id == victim.id
        assert len(_links(db, victim.id)) == 1
