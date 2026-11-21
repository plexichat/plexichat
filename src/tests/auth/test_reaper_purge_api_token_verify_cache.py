"""Regression: purging an account must drop its warm API-token verify verdict.

``verify_api_access_token`` memoises its read-only verdict in a process-local
bounded LRU for up to 10 seconds. ``AccountReaper.purge_user`` deletes the
user's API token rows through ``erase_user_residue`` but never cleared that
cache, so a token used shortly before the purge kept authenticating after its
row (and the owning account) were gone. The account is erased; the credential
must die with it, not at cache eviction.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth._lazy import _get_auth_manager
from src.core.auth.deletion_log import DeletionLog
from src.core.auth.managers.api_token import _invalidate_api_token_verify_cache
from src.core.auth.reaper import AccountReaper
from src.utils import encryption

pytestmark = pytest.mark.auth


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    _invalidate_api_token_verify_cache()
    yield
    _invalidate_api_token_verify_cache()


_IP = "203.0.113.44"
_UA = "reaper-purge-regression/1.0"


def test_purge_drops_warm_api_token_verify_verdict(db, auth_manager, pii_gen, tmp_path):
    mgr = _get_auth_manager().get_instance()
    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"purge_token_{unique}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    token = mgr.create_api_access_token("doomed", created_by=int(user.id))

    # Warm the in-process verdict while the token still exists.
    assert (
        mgr.verify_api_access_token(
            token.token, ip_address=_IP, user_agent=_UA, path="/api/v1/users/@me"
        )
        is True
    )

    reaper = AccountReaper(db, auth_manager)
    reaper._log = DeletionLog(file_path=str(tmp_path / "deletion_log.jsonl"))
    reaper.purge_user(int(user.id), user.username)

    # The credential row is gone with the account ...
    assert (
        db.fetch_one("SELECT id FROM auth_api_access_tokens WHERE id = ?", (token.id,))
        is None
    )
    assert (
        db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (int(user.id),)) is None
    )

    # ... so the warm verdict must not keep it valid after the purge.
    assert (
        mgr.verify_api_access_token(
            token.token, ip_address=_IP, user_agent=_UA, path="/api/v1/users/@me"
        )
        is False
    )
