"""PushManager — token registration, unregister, bulk, max-tokens-per-user."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def push_manager_setup(db):
    from src.core.push import setup as push_module_setup
    from src.core.push.manager import PushManager

    push_module_setup(db, notifications_module=None)
    return PushManager(db, notifications_module=None)


class TestPushManagerRegistration:
    def test_register_token(self, push_manager_setup, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="pushuser1",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        token_record = push_manager_setup.register_token(
            user_id=user.id,
            token="test-fcm-token-aaaaaaaaaaaaaaaaaaaa",
            platform="android",
        )
        assert token_record is not None
        assert token_record["platform"] == "android"

    def test_register_unsupported_platform_raises(self, push_manager_setup):
        with pytest.raises(ValueError):
            push_manager_setup.register_token(
                user_id=1, token="x", platform="flipphone"
            )

    def test_register_token_per_user_limit_enforced(
        self, push_manager_setup, auth_manager, pii_gen
    ):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.push.manager import PushManager

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="pushlimit",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        for i in range(PushManager.MAX_TOKENS_PER_USER):
            push_manager_setup.register_token(
                user_id=user.id, token=f"push-token-{i:02d}", platform="ios"
            )
        # Inserting one more should silently evict the oldest.
        push_manager_setup.register_token(
            user_id=user.id, token="push-token-NEW", platform="ios"
        )
        rows = push_manager_setup.get_user_tokens(user_id=user.id)
        assert len(rows) == PushManager.MAX_TOKENS_PER_USER
        assert any(r["token"] == "push-token-NEW" for r in rows)

    def test_unregister_token(self, push_manager_setup, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="pushunreg",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        push_manager_setup.register_token(
            user_id=user.id, token="to-be-removed", platform="web"
        )
        assert (
            push_manager_setup.unregister_token(user_id=user.id, token="to-be-removed")
            is True
        )

    def test_get_user_tokens_filter_platform(
        self, push_manager_setup, auth_manager, pii_gen
    ):
        from unittest.mock import patch

        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="pushlist",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        push_manager_setup.register_token(
            user_id=user.id, token="t1", platform="android"
        )
        push_manager_setup.register_token(user_id=user.id, token="t2", platform="ios")
        tokens = push_manager_setup.get_user_tokens(user_id=user.id)
        platforms = {t["platform"] for t in tokens}
        assert {"android", "ios"} <= platforms

    def test_send_bulk_push_no_tokens_returns_zero(self, push_manager_setup):
        sent = push_manager_setup.send_bulk_push(user_ids=[99999], title="t", body="b")
        assert sent == 0
