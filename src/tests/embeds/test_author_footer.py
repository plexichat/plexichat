"""
Tests for embed author and footer sections.
"""

import pytest
from src.core.embeds import EmbedValidationError
from unittest.mock import patch


class TestEmbedAuthor:
    """Tests for embed author section."""

    def test_create_embed_with_author_name(self, db, auth_manager):
        """Test creating embed with author name only."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="auth1_test",
                email="auth1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Author Test", author={"name": "John Doe"}
        )

        assert embed.author is not None
        assert embed.author.name == "John Doe"
        assert embed.author.url is None
        assert embed.author.icon_url is None

    def test_create_embed_with_author_url(self, db, auth_manager):
        """Test creating embed with author name and URL."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="auth2_test",
                email="auth2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Author URL Test",
            author={"name": "Jane Doe", "url": "https://jane.example.com"},
        )

        assert embed.author.name == "Jane Doe"
        assert embed.author.url == "https://jane.example.com"

    def test_create_embed_with_author_icon(self, db, auth_manager):
        """Test creating embed with author icon."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="auth3_test",
                email="auth3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Author Icon Test",
            author={"name": "Bob Smith", "icon_url": "https://example.com/avatar.png"},
        )

        assert embed.author.icon_url == "https://example.com/avatar.png"

    def test_create_embed_with_full_author(self, db, auth_manager):
        """Test creating embed with all author fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="auth4_test",
                email="auth4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Full Author",
            author={
                "name": "Alice Johnson",
                "url": "https://alice.example.com",
                "icon_url": "https://alice.example.com/avatar.png",
            },
        )

        assert embed.author.name == "Alice Johnson"
        assert embed.author.url == "https://alice.example.com"
        assert embed.author.icon_url == "https://alice.example.com/avatar.png"

    def test_author_name_max_length(self, db, auth_manager):
        """Test author name at max length (256 chars)."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="auth5_test",
                email="auth5_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        name = "a" * 256

        embed = embeds._manager.create_embed(
            user_id=user.id, title="Long Author Name", author={"name": name}
        )

        assert len(embed.author.name) == 256

    def test_author_name_exceeds_max_length(self, db, auth_manager):
        """Test author name exceeding max length fails."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="auth6_test",
                email="auth6_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        name = "a" * 257

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id, title="Too Long Author", author={"name": name}
            )

    def test_author_url_validation(self, db, auth_manager):
        """Test author URL must be valid."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="auth7_test",
                email="auth7_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Invalid Author URL",
                author={"name": "Test", "url": "not-a-url"},
            )

    def test_author_icon_url_validation(self, db, auth_manager):
        """Test author icon URL must be valid."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="auth8_test",
                email="auth8_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Invalid Author Icon",
                author={"name": "Test", "icon_url": "javascript:alert('xss')"},
            )


class TestEmbedFooter:
    """Tests for embed footer section."""

    def test_create_embed_with_footer_text(self, db, auth_manager):
        """Test creating embed with footer text only."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="foot1_test",
                email="foot1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Footer Test", footer={"text": "Footer text here"}
        )

        assert embed.footer is not None
        assert embed.footer.text == "Footer text here"
        assert embed.footer.icon_url is None

    def test_create_embed_with_footer_icon(self, db, auth_manager):
        """Test creating embed with footer text and icon."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="foot2_test",
                email="foot2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Footer Icon Test",
            footer={
                "text": "Footer with icon",
                "icon_url": "https://example.com/icon.png",
            },
        )

        assert embed.footer.text == "Footer with icon"
        assert embed.footer.icon_url == "https://example.com/icon.png"

    def test_footer_text_max_length(self, db, auth_manager):
        """Test footer text at max length (2048 chars)."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="foot3_test",
                email="foot3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        text = "a" * 2048

        embed = embeds._manager.create_embed(
            user_id=user.id, title="Long Footer", footer={"text": text}
        )

        assert len(embed.footer.text) == 2048

    def test_footer_text_exceeds_max_length(self, db, auth_manager):
        """Test footer text exceeding max length fails."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="foot4_test",
                email="foot4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        text = "a" * 2049

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id, title="Too Long Footer", footer={"text": text}
            )

    def test_footer_icon_url_validation(self, db, auth_manager):
        """Test footer icon URL must be valid."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="foot5_test",
                email="foot5_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Invalid Footer Icon",
                footer={"text": "Test", "icon_url": "data:image/png;base64,abc"},
            )


class TestAuthorAndFooterTogether:
    """Tests for using both author and footer."""

    def test_embed_with_both_author_and_footer(self, db, auth_manager):
        """Test embed with both author and footer."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="both1_test",
                email="both1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Author and Footer",
            author={"name": "Author Name", "url": "https://author.com"},
            footer={"text": "Footer text", "icon_url": "https://example.com/icon.png"},
        )

        assert embed.author is not None
        assert embed.footer is not None
        assert embed.author.name == "Author Name"
        assert embed.footer.text == "Footer text"

    def test_embed_author_without_footer(self, db, auth_manager):
        """Test embed with author but no footer."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="both2_test",
                email="both2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Author Only", author={"name": "Author"}
        )

        assert embed.author is not None
        assert embed.footer is None

    def test_embed_footer_without_author(self, db, auth_manager):
        """Test embed with footer but no author."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="both3_test",
                email="both3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Footer Only", footer={"text": "Footer"}
        )

        assert embed.author is None
        assert embed.footer is not None


class TestAuthorFooterCharacterCount:
    """Tests for author and footer contributing to total character count."""

    def test_author_name_counts_toward_total(self, db, auth_manager):
        """Test author name counts toward total character limit."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="cnt1_test",
                email="cnt1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        # Create embed near limit with author name
        title = "a" * 256
        desc = "b" * 4096
        author_name = "c" * 256  # 256 + 4096 + 256 = 4608

        embed = embeds._manager.create_embed(
            user_id=user.id, title=title, description=desc, author={"name": author_name}
        )

        assert embed is not None

    def test_footer_text_counts_toward_total(self, db, auth_manager):
        """Test footer text counts toward total character limit."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="cnt2_test",
                email="cnt2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        # Create embed near limit with footer
        title = "a" * 256
        desc = "b" * 4096
        footer_text = "c" * 1648  # 256 + 4096 + 1648 = 6000

        embed = embeds._manager.create_embed(
            user_id=user.id, title=title, description=desc, footer={"text": footer_text}
        )

        assert embed is not None

    def test_author_and_footer_exceed_total(self, db, auth_manager):
        """Test author and footer together can exceed total limit."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="cnt3_test",
                email="cnt3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        # Create embed that exceeds limit
        title = "a" * 256
        desc = "b" * 4096
        author_name = "c" * 256
        footer_text = "d" * 2000  # 256 + 4096 + 256 + 2000 = 6608 > 6000

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title=title,
                description=desc,
                author={"name": author_name},
                footer={"text": footer_text},
            )
