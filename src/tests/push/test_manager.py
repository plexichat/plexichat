"""PushManager — token registration, unregister, bulk, max-tokens-per-user."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestPushManagerRegistration:
    def test_register_token(self, db, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.push import setup
        from src.core.push.manager import PushManager

        setup(db, notifications_module=None)
        mgr = PushManager(db, notifications_module=None)

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="pushuser1",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        token_record = mgr.register_token(
            user_id=user.id,
            token="test-fcm-token-aaaaaaaaaaaaaaaaaaaa",
            platform="android",
        )
        assert token_record is not None
        assert token_record["platform"] == "android"

    def test_register_unsupported_platform_raises(self, db):
        from src.core.push.manager import PushManager

        mgr = PushManager(db)
        with pytest.raises(ValueError):
            mgr.register_token(user_id=1, token="x", platform="flipphone")

    def test_register_token_per_user_limit_enforced(self, db, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.push import setup
        from src.core.push.manager import PushManager

        setup(db, notifications_module=None)
        mgr = PushManager(db, notifications_module=None)

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="pushlimit",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        for i in range(PushManager.MAX_TOKENS_PER_USER):
            mgr.register_token(
                user_id=user.id,
                token=f"push-token-{i:02d}",
                platform="ios",
            )
        # Inserting one more should evict the oldest token, not raise.
        evicted = mgr.register_token(
            user_id=user.id,
            token="push-token-NEW",
            platform="ios",
        )
        rows = mgr.get_user_tokens(user_id=user.id)
        assert len(rows) == PushManager.MAX_TOKENS_PER_USER
        assert any(r["token"] == "push-token-NEW" for r in rows)

    def test_unregister_token(self, db, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.push.setup import setup
        from src.core.push.manager import PushManager

        setup(db, notifications_module=None)
        mgr = PushManager(db, notifications_module=None)

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="pushunreg",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        mgr.register_token(
            user_id=user.id,
            token="to-be-removed",
            platform="web",
        )
        assert mgr.unregister_token(user_id=user.id, token="to-be-removed") is True

    def test_get_user_tokens(self, db, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.push import setup
        from src.core.push.manager import PushManager

        setup(db, notifications_module=None)
        mgr = PushManager(db, notifications_module=None)

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="pushlist",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        mgr.register_token(user_id=user.id, token="t1", platform="android")
        mgr.register_token(user_id=user.id, token="t2", platform="ios")
        tokens = mgr.get_user_tokens(user_id=user.id)
        assert any(t["token"] == "t1" for t in tokens)
        assert any(t["token"] == "t2" for t in tokens)

    def test_send_bulk_push_no_tokens_returns_zero(self, db):
        from src.core.push import setup
        from src.core.push.manager import PushManager

        setup(db, notifications_module=None)
        mgr = PushManager(db, notifications_module=None)
        sent = mgr.send_bulk_push(user_ids=[99999], title="t", body="b")
        assert sent == 0
