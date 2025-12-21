"""
Tests for embed author and footer sections.
"""

import pytest
from src.core.embeds import EmbedValidationError


class TestEmbedAuthor:
    """Tests for embed author section."""

    def test_create_embed_with_author_name(self, db_and_modules):
        """Test creating embed with author name only."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"auth1_{unique_id}",
            email=f"auth1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Author Test",
            author={"name": "John Doe"}
        )

        assert embed.author is not None
        assert embed.author.name == "John Doe"
        assert embed.author.url is None
        assert embed.author.icon_url is None

    def test_create_embed_with_author_url(self, db_and_modules):
        """Test creating embed with author name and URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"auth2_{unique_id}",
            email=f"auth2_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Author URL Test",
            author={
                "name": "Jane Doe",
                "url": "https://jane.example.com"
            }
        )

        assert embed.author.name == "Jane Doe"
        assert embed.author.url == "https://jane.example.com"

    def test_create_embed_with_author_icon(self, db_and_modules):
        """Test creating embed with author icon."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"auth3_{unique_id}",
            email=f"auth3_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Author Icon Test",
            author={
                "name": "Bob Smith",
                "icon_url": "https://example.com/avatar.png"
            }
        )

        assert embed.author.icon_url == "https://example.com/avatar.png"

    def test_create_embed_with_full_author(self, db_and_modules):
        """Test creating embed with all author fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"auth4_{unique_id}",
            email=f"auth4_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Full Author",
            author={
                "name": "Alice Johnson",
                "url": "https://alice.example.com",
                "icon_url": "https://alice.example.com/avatar.png"
            }
        )

        assert embed.author.name == "Alice Johnson"
        assert embed.author.url == "https://alice.example.com"
        assert embed.author.icon_url == "https://alice.example.com/avatar.png"

    def test_author_name_max_length(self, db_and_modules):
        """Test author name at max length (256 chars)."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"auth5_{unique_id}",
            email=f"auth5_{unique_id}@example.com",
            password="TestPass123!"
        )

        name = "a" * 256

        embed = embeds.create_embed(
            user_id=user.id,
            title="Long Author Name",
            author={"name": name}
        )

        assert len(embed.author.name) == 256

    def test_author_name_exceeds_max_length(self, db_and_modules):
        """Test author name exceeding max length fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"auth6_{unique_id}",
            email=f"auth6_{unique_id}@example.com",
            password="TestPass123!"
        )

        name = "a" * 257

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Too Long Author",
                author={"name": name}
            )

    def test_author_url_validation(self, db_and_modules):
        """Test author URL must be valid."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"auth7_{unique_id}",
            email=f"auth7_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Invalid Author URL",
                author={"name": "Test", "url": "not-a-url"}
            )

    def test_author_icon_url_validation(self, db_and_modules):
        """Test author icon URL must be valid."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"auth8_{unique_id}",
            email=f"auth8_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Invalid Author Icon",
                author={"name": "Test", "icon_url": "javascript:alert('xss')"}
            )


class TestEmbedFooter:
    """Tests for embed footer section."""

    def test_create_embed_with_footer_text(self, db_and_modules):
        """Test creating embed with footer text only."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"foot1_{unique_id}",
            email=f"foot1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Footer Test",
            footer={"text": "Footer text here"}
        )

        assert embed.footer is not None
        assert embed.footer.text == "Footer text here"
        assert embed.footer.icon_url is None

    def test_create_embed_with_footer_icon(self, db_and_modules):
        """Test creating embed with footer text and icon."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"foot2_{unique_id}",
            email=f"foot2_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Footer Icon Test",
            footer={
                "text": "Footer with icon",
                "icon_url": "https://example.com/icon.png"
            }
        )

        assert embed.footer.text == "Footer with icon"
        assert embed.footer.icon_url == "https://example.com/icon.png"

    def test_footer_text_max_length(self, db_and_modules):
        """Test footer text at max length (2048 chars)."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"foot3_{unique_id}",
            email=f"foot3_{unique_id}@example.com",
            password="TestPass123!"
        )

        text = "a" * 2048

        embed = embeds.create_embed(
            user_id=user.id,
            title="Long Footer",
            footer={"text": text}
        )

        assert len(embed.footer.text) == 2048

    def test_footer_text_exceeds_max_length(self, db_and_modules):
        """Test footer text exceeding max length fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"foot4_{unique_id}",
            email=f"foot4_{unique_id}@example.com",
            password="TestPass123!"
        )

        text = "a" * 2049

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Too Long Footer",
                footer={"text": text}
            )

    def test_footer_icon_url_validation(self, db_and_modules):
        """Test footer icon URL must be valid."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"foot5_{unique_id}",
            email=f"foot5_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Invalid Footer Icon",
                footer={"text": "Test", "icon_url": "data:image/png;base64,abc"}
            )


class TestAuthorAndFooterTogether:
    """Tests for using both author and footer."""

    def test_embed_with_both_author_and_footer(self, db_and_modules):
        """Test embed with both author and footer."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"both1_{unique_id}",
            email=f"both1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Author and Footer",
            author={"name": "Author Name", "url": "https://author.com"},
            footer={"text": "Footer text", "icon_url": "https://example.com/icon.png"}
        )

        assert embed.author is not None
        assert embed.footer is not None
        assert embed.author.name == "Author Name"
        assert embed.footer.text == "Footer text"

    def test_embed_author_without_footer(self, db_and_modules):
        """Test embed with author but no footer."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"both2_{unique_id}",
            email=f"both2_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Author Only",
            author={"name": "Author"}
        )

        assert embed.author is not None
        assert embed.footer is None

    def test_embed_footer_without_author(self, db_and_modules):
        """Test embed with footer but no author."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"both3_{unique_id}",
            email=f"both3_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Footer Only",
            footer={"text": "Footer"}
        )

        assert embed.author is None
        assert embed.footer is not None


class TestAuthorFooterCharacterCount:
    """Tests for author and footer contributing to total character count."""

    def test_author_name_counts_toward_total(self, db_and_modules):
        """Test author name counts toward total character limit."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"cnt1_{unique_id}",
            email=f"cnt1_{unique_id}@example.com",
            password="TestPass123!"
        )

        # Create embed near limit with author name
        title = "a" * 256
        desc = "b" * 4096
        author_name = "c" * 256  # 256 + 4096 + 256 = 4608

        embed = embeds.create_embed(
            user_id=user.id,
            title=title,
            description=desc,
            author={"name": author_name}
        )

        assert embed is not None

    def test_footer_text_counts_toward_total(self, db_and_modules):
        """Test footer text counts toward total character limit."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"cnt2_{unique_id}",
            email=f"cnt2_{unique_id}@example.com",
            password="TestPass123!"
        )

        # Create embed near limit with footer
        title = "a" * 256
        desc = "b" * 4096
        footer_text = "c" * 1648  # 256 + 4096 + 1648 = 6000

        embed = embeds.create_embed(
            user_id=user.id,
            title=title,
            description=desc,
            footer={"text": footer_text}
        )

        assert embed is not None

    def test_author_and_footer_exceed_total(self, db_and_modules):
        """Test author and footer together can exceed total limit."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"cnt3_{unique_id}",
            email=f"cnt3_{unique_id}@example.com",
            password="TestPass123!"
        )

        # Create embed that exceeds limit
        title = "a" * 256
        desc = "b" * 4096
        author_name = "c" * 256
        footer_text = "d" * 2000  # 256 + 4096 + 256 + 2000 = 6608 > 6000

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title=title,
                description=desc,
                author={"name": author_name},
                footer={"text": footer_text}
            )
