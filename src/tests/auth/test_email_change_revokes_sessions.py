"""An email change must retire every session created under the old address.

Email is the account's recovery identifier and the destination for
password-reset links. ``update_user`` already demotes ``email_verified`` and
retires outstanding verification tokens when the address changes, but it left
the sessions themselves alive. A stale (or hijacked) session therefore kept
full authenticated access after the account moved to a new address, and the
victim could not evict it by changing email. Password change/reset already
revoke sessions; an address change is the same class of credential transition.

Regression: PATCH /users/@me with a new email must not leave the old session
usable, while unrelated profile edits (username only) must keep it valid.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.utils import encryption


def _register_and_login(auth_manager, label):
    name = f"{label}_{uuid.uuid4().hex[:8]}"
    email = f"{name}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(name, email, "TestPass123!")
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(name, "TestPass123!")
    return user, result.token


@pytest.mark.auth
class TestEmailChangeRevokesSessions:
    def test_email_change_revokes_preexisting_session(self, db, auth_manager):
        user, token = _register_and_login(auth_manager, "email_revoke")
        # Baseline: the session is live before the address changes.
        assert auth_manager.verify_token(token).valid is True

        new_email = f"moved_{uuid.uuid4().hex[:8]}@test.local"
        auth_manager.update_user(user.id, email=new_email)

        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(token)

    def test_username_only_change_keeps_session(self, db, auth_manager):
        # Control: a non-credential profile edit must not log the user out.
        user, token = _register_and_login(auth_manager, "user_keep")
        auth_manager.update_user(user.id, username=f"renamed_{user.username}")
        assert auth_manager.verify_token(token).valid is True
