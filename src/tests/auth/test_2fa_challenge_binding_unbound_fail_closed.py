"""Regression: 2FA challenge IP/UA binding must fail closed on a missing index.

``complete_2fa`` compared the presented IP/UA blind indexes against the stored
``ip_index``/``ua_index`` only when those row fields happened to be truthy. A
challenge minted without a recorded IP/UA (password/OAuth/passkey login with no
context, a legacy row, or a failed index write) therefore had NO binding
enforced at completion: a stolen challenge token could be redeemed from ANY
address, silently disabling the very control that exists to prevent replay.
This is the 2FA-challenge twin of the session-token fail-open fixed for
``_verify_session_token``.

These tests prove the stored index is required when the caller presents
context, while a correctly bound challenge still verifies from the same context
and still fails from a different one (the checks are hardened, not weakened).
"""

from unittest.mock import patch

import pyotp
import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.models import AuthStatus
from src.utils import encryption

pytestmark = [pytest.mark.security, pytest.mark.unit]

IP = "203.0.113.10"
UA = "legit-browser/1.0"
OTHER_IP = "198.51.100.77"


def _make_2fa_user(auth_manager, username):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username, f"{username}@example.com", "TestPass123!"
        )
    setup = auth_manager.setup_2fa(user.id)
    totp = pyotp.TOTP(setup.secret)
    auth_manager.confirm_2fa(user.id, totp.now())
    return user, totp


def _challenge(auth_manager, user_id, ip, ua):
    return auth_manager._get_manager()._create_2fa_challenge(user_id, None, ip, ua)


def test_unbound_challenge_rejected_when_presenting_context(auth_manager):
    user, totp = _make_2fa_user(auth_manager, "binding_unbound")
    challenge = _challenge(auth_manager, user.id, None, None)

    # Pre-condition: the row really carries no stored binding index.
    row = auth_manager._get_manager()._db.fetch_one(
        "SELECT ip_index, ua_index FROM auth_2fa_challenges WHERE id = ?",
        (challenge.id,),
    )
    assert row["ip_index"] is None and row["ua_index"] is None

    # The binding comparison used to be skipped entirely here, letting the
    # challenge be completed from any address. It must now fail closed.
    with pytest.raises(TokenInvalidError):
        auth_manager.complete_2fa(
            challenge.token, totp.now(), ip_address=IP, user_agent=UA
        )


def test_empty_stored_index_rejected(auth_manager):
    user, totp = _make_2fa_user(auth_manager, "binding_empty")
    challenge = _challenge(auth_manager, user.id, IP, UA)

    # An empty stored index is just as unusable as a missing one.
    auth_manager._get_manager()._db.execute(
        "UPDATE auth_2fa_challenges SET ip_index = '' WHERE id = ?",
        (challenge.id,),
    )
    with pytest.raises(TokenInvalidError):
        auth_manager.complete_2fa(
            challenge.token, totp.now(), ip_address=IP, user_agent=UA
        )


def test_bound_challenge_still_verifies_from_same_context(auth_manager):
    user, totp = _make_2fa_user(auth_manager, "binding_good")
    challenge = _challenge(auth_manager, user.id, IP, UA)

    result = auth_manager.complete_2fa(
        challenge.token, totp.now(), ip_address=IP, user_agent=UA
    )
    assert result.status is AuthStatus.SUCCESS


def test_bound_challenge_rejected_from_other_context(auth_manager):
    user, totp = _make_2fa_user(auth_manager, "binding_other")
    challenge = _challenge(auth_manager, user.id, IP, UA)

    with pytest.raises(TokenInvalidError):
        auth_manager.complete_2fa(
            challenge.token, totp.now(), ip_address=OTHER_IP, user_agent=UA
        )
