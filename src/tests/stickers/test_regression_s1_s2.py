"""Regression tests for sticker bug fixes (S1, S2)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestPersonalPackLimitS2:
    def test_personal_pack_limit_enforced(self, sticker_manager, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.stickers.exceptions import PackLimitError
        from src.core.stickers.models import PackType

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="personalpacklimit",
                email=pii_gen.email(),
                password="TestPass123!",
            )

        # Force a tiny per-user limit so the test is deterministic.
        sticker_manager._config["max_packs_per_user"] = 2

        for i in range(2):
            pack = sticker_manager.create_pack(
                user_id=user.id,
                name=f"Personal Pack {i}",
                pack_type=PackType.PERSONAL,
            )
            assert pack is not None

        with pytest.raises(PackLimitError):
            sticker_manager.create_pack(
                user_id=user.id,
                name="One Too Many",
                pack_type=PackType.PERSONAL,
            )


class TestSuggestionsIncludePersonalS1:
    def test_personal_stickers_are_suggested(
        self, sticker_manager, auth_manager, pii_gen
    ):
        from unittest.mock import patch

        from src.utils import encryption
        from src.core.stickers.models import PackType, StickerFormat

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="suggestionsuser",
                email=pii_gen.email(),
                password="TestPass123!",
            )

        pack = sticker_manager.create_pack(
            user_id=user.id,
            name="My Personal Pack",
            pack_type=PackType.PERSONAL,
        )
        sticker_manager.add_sticker(
            user_id=user.id,
            pack_id=pack.id,
            name="celebrate",
            format=StickerFormat.PNG,
            url="https://example.test/stickers/celebrate.png",
            size=10,
            tags=["party", "congrats"],
            related_emoji="🎉",
        )

        suggestions = sticker_manager.get_sticker_suggestions(
            user_id=user.id, content="lets party and celebrate!"
        )
        names = {s.sticker.name for s in suggestions}
        assert "celebrate" in names
