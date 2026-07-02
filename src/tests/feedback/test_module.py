"""Feedback module — submit / get_feedback_by_id / custom_category."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestFeedback:
    def test_submit_and_lookup(self, db, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.feedback import (
            setup as feedback_setup,
            submit_feedback,
            get_feedback_by_id,
        )

        feedback_setup(db)

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="feedbackuser",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        fid = submit_feedback(
            user_id=user.id,
            content="Test feedback message",
            category="ui",
            rating=4,
        )
        assert fid > 0
        entry = get_feedback_by_id(fid)
        assert entry is not None
        assert entry.user_id == user.id
        assert entry.content == "Test feedback message"
        assert entry.category == "ui"
        assert entry.rating == 4
        assert entry.status == "open"

    def test_get_feedback_missing_returns_none(self, db):
        from src.core.feedback import setup, get_feedback_by_id

        setup(db)
        assert get_feedback_by_id(999_999_999) is None
