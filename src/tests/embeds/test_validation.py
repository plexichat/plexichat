"""
Tests for embed validation - field limits, total char limits.
"""

import pytest
from src.core.embeds import (
    EmbedValidationError,
)


class TestTitleValidation:
    """Tests for title validation."""

    def test_title_max_length(self, db_and_modules):
        """Test title at max length (256 chars)."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val1_{unique_id}",
            email=f"val1_{unique_id}@example.com",
            password="TestPass123!",
        )

        title = "a" * 256
        embed = embeds.create_embed(user_id=user.id, title=title)

        assert len(embed.title) == 256

    def test_title_exceeds_max_length(self, db_and_modules):
        """Test title exceeding max length fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val2_{unique_id}",
            email=f"val2_{unique_id}@example.com",
            password="TestPass123!",
        )

        title = "a" * 257

        with pytest.raises(EmbedValidationError) as exc_info:
            embeds.create_embed(user_id=user.id, title=title)

        assert "title" in str(exc_info.value).lower() or any(
            "title" in i.lower() for i in exc_info.value.issues
        )


class TestDescriptionValidation:
    """Tests for description validation."""

    def test_description_max_length(self, db_and_modules):
        """Test description at max length (4096 chars)."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val3_{unique_id}",
            email=f"val3_{unique_id}@example.com",
            password="TestPass123!",
        )

        desc = "a" * 4096
        embed = embeds.create_embed(user_id=user.id, description=desc)

        assert len(embed.description) == 4096

    def test_description_exceeds_max_length(self, db_and_modules):
        """Test description exceeding max length fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val4_{unique_id}",
            email=f"val4_{unique_id}@example.com",
            password="TestPass123!",
        )

        desc = "a" * 4097

        with pytest.raises(EmbedValidationError) as exc_info:
            embeds.create_embed(user_id=user.id, description=desc)

        assert len(exc_info.value.issues) > 0


class TestUrlValidation:
    """Tests for URL validation."""

    def test_valid_https_url(self, db_and_modules):
        """Test valid HTTPS URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val5_{unique_id}",
            email=f"val5_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id, title="URL Test", url="https://example.com/path?query=1"
        )

        assert embed.url == "https://example.com/path?query=1"

    def test_valid_http_url(self, db_and_modules):
        """Test valid HTTP URL."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val6_{unique_id}",
            email=f"val6_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id, title="HTTP Test", url="http://example.com"
        )

        assert embed.url == "http://example.com"

    def test_javascript_url_rejected(self, db_and_modules):
        """Test JavaScript URL is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val7_{unique_id}",
            email=f"val7_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id, title="JS Test", url="javascript:alert('xss')"
            )

    def test_data_url_rejected(self, db_and_modules):
        """Test data URL is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val8_{unique_id}",
            email=f"val8_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Data Test",
                url="data:text/html,<script>alert('xss')</script>",
            )

    def test_invalid_url_format(self, db_and_modules):
        """Test invalid URL format is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val9_{unique_id}",
            email=f"val9_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id, title="Invalid URL", url="not-a-valid-url"
            )


class TestColorValidation:
    """Tests for color validation."""

    def test_valid_hex_color_with_hash(self, db_and_modules):
        """Test valid hex color with hash."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val10_{unique_id}",
            email=f"val10_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id, title="Color Test", color="#FF5733"
        )

        assert embed.color == "#FF5733"

    def test_valid_hex_color_without_hash(self, db_and_modules):
        """Test valid hex color without hash."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val11_{unique_id}",
            email=f"val11_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(user_id=user.id, title="Color Test", color="00FF00")

        assert embed.color == "#00FF00"

    def test_valid_short_hex_color(self, db_and_modules):
        """Test valid 3-char hex color."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val12_{unique_id}",
            email=f"val12_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(user_id=user.id, title="Short Color", color="#F00")

        assert embed.color == "#FF0000"

    def test_invalid_color_format(self, db_and_modules):
        """Test invalid color format is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val13_{unique_id}",
            email=f"val13_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id, title="Invalid Color", color="not-a-color"
            )

    def test_invalid_hex_color(self, db_and_modules):
        """Test invalid hex characters rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val14_{unique_id}",
            email=f"val14_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(user_id=user.id, title="Invalid Hex", color="#GGGGGG")


class TestTimestampValidation:
    """Tests for timestamp validation."""

    def test_valid_iso8601_timestamp(self, db_and_modules):
        """Test valid ISO8601 timestamp."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val15_{unique_id}",
            email=f"val15_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id, title="Timestamp Test", timestamp="2025-01-15T12:00:00Z"
        )

        assert embed.timestamp == "2025-01-15T12:00:00Z"

    def test_valid_date_only_timestamp(self, db_and_modules):
        """Test valid date-only timestamp."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val16_{unique_id}",
            email=f"val16_{unique_id}@example.com",
            password="TestPass123!",
        )

        embed = embeds.create_embed(
            user_id=user.id, title="Date Test", timestamp="2025-01-15"
        )

        assert embed.timestamp == "2025-01-15"

    def test_invalid_timestamp_format(self, db_and_modules):
        """Test invalid timestamp format is rejected."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val17_{unique_id}",
            email=f"val17_{unique_id}@example.com",
            password="TestPass123!",
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id, title="Invalid Timestamp", timestamp="not-a-timestamp"
            )


class TestTotalCharacterLimit:
    """Tests for total character limit (6000)."""

    def test_total_chars_at_limit(self, db_and_modules):
        """Test embed at total character limit."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val18_{unique_id}",
            email=f"val18_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create embed with exactly 6000 chars
        title = "a" * 256
        desc = "b" * 4096
        footer_text = "c" * 1648  # 256 + 4096 + 1648 = 6000

        embed = embeds.create_embed(
            user_id=user.id, title=title, description=desc, footer={"text": footer_text}
        )

        assert embed is not None

    def test_total_chars_exceeds_limit(self, db_and_modules):
        """Test embed exceeding total character limit fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"val19_{unique_id}",
            email=f"val19_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create embed with more than 6000 chars
        title = "a" * 256
        desc = "b" * 4096
        footer_text = "c" * 2000  # 256 + 4096 + 2000 = 6352 > 6000

        with pytest.raises(EmbedValidationError) as exc_info:
            embeds.create_embed(
                user_id=user.id,
                title=title,
                description=desc,
                footer={"text": footer_text},
            )

        assert any(
            "6000" in issue or "character" in issue.lower()
            for issue in exc_info.value.issues
        )


class TestValidateEmbedFunction:
    """Tests for validate_embed function."""

    def test_validate_valid_embed(self, db_and_modules):
        """Test validating valid embed data."""
        db, auth, messaging, servers, embeds = db_and_modules

        result = embeds.validate_embed(
            {"title": "Valid Title", "description": "Valid description"}
        )

        assert result["valid"] is True
        assert len(result["issues"]) == 0
        assert result["total_chars"] > 0

    def test_validate_invalid_embed(self, db_and_modules):
        """Test validating invalid embed data."""
        db, auth, messaging, servers, embeds = db_and_modules

        result = embeds.validate_embed(
            {
                "title": "a" * 300,  # Too long
                "url": "invalid-url",
            }
        )

        assert result["valid"] is False
        assert len(result["issues"]) > 0

    def test_validate_returns_total_chars(self, db_and_modules):
        """Test validate returns total character count."""
        db, auth, messaging, servers, embeds = db_and_modules

        result = embeds.validate_embed(
            {
                "title": "Hello",  # 5 chars
                "description": "World",  # 5 chars
            }
        )

        assert result["total_chars"] == 10

        assert result["total_chars"] == 10
