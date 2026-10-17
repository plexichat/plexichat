"""
Tests for embed fields (name, value, inline).
"""

import pytest
from src.core.embeds import EmbedValidationError
from unittest.mock import patch


class TestCreateEmbedWithFields:
    """Tests for creating embeds with fields."""

    def test_create_embed_with_single_field(self, db, auth_manager):
        """Test creating embed with single field."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld1_test",
                email="fld1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Field Test",
            fields=[{"name": "Field Name", "value": "Field Value"}],
        )

        assert len(embed.fields) == 1
        assert embed.fields[0].name == "Field Name"
        assert embed.fields[0].value == "Field Value"
        assert embed.fields[0].inline is False

    def test_create_embed_with_inline_field(self, db, auth_manager):
        """Test creating embed with inline field."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld2_test",
                email="fld2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Inline Field Test",
            fields=[{"name": "Inline Field", "value": "Inline Value", "inline": True}],
        )

        assert embed.fields[0].inline is True

    def test_create_embed_with_multiple_fields(self, db, auth_manager):
        """Test creating embed with multiple fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld3_test",
                email="fld3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Multiple Fields",
            fields=[
                {"name": "Field 1", "value": "Value 1", "inline": True},
                {"name": "Field 2", "value": "Value 2", "inline": True},
                {"name": "Field 3", "value": "Value 3", "inline": False},
            ],
        )

        assert len(embed.fields) == 3
        assert embed.fields[0].name == "Field 1"
        assert embed.fields[1].name == "Field 2"
        assert embed.fields[2].name == "Field 3"

    def test_create_embed_with_max_fields(self, db, auth_manager):
        """Test creating embed with maximum 25 fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld4_test",
                email="fld4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        fields = [{"name": f"Field {i}", "value": f"Value {i}"} for i in range(25)]

        embed = embeds._manager.create_embed(
            user_id=user.id, title="Max Fields", fields=fields
        )

        assert len(embed.fields) == 25

    def test_create_embed_exceeds_max_fields(self, db, auth_manager):
        """Test creating embed with more than 25 fields fails."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld5_test",
                email="fld5_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        fields = [{"name": f"Field {i}", "value": f"Value {i}"} for i in range(26)]

        with pytest.raises(EmbedValidationError) as exc_info:
            embeds._manager.create_embed(
                user_id=user.id, title="Too Many Fields", fields=fields
            )

        assert any(
            "25" in issue or "field" in issue.lower() for issue in exc_info.value.issues
        )


class TestFieldNameValidation:
    """Tests for field name validation."""

    def test_field_name_max_length(self, db, auth_manager):
        """Test field name at max length (256 chars)."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld6_test",
                email="fld6_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        name = "a" * 256

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Long Field Name",
            fields=[{"name": name, "value": "Value"}],
        )

        assert len(embed.fields[0].name) == 256

    def test_field_name_exceeds_max_length(self, db, auth_manager):
        """Test field name exceeding max length fails."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld7_test",
                email="fld7_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        name = "a" * 257

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Too Long Field Name",
                fields=[{"name": name, "value": "Value"}],
            )

    def test_field_name_required(self, db, auth_manager):
        """Test field name is required."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld8_test",
                email="fld8_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Missing Field Name",
                fields=[{"value": "Value only"}],
            )


class TestFieldValueValidation:
    """Tests for field value validation."""

    def test_field_value_max_length(self, db, auth_manager):
        """Test field value at max length (1024 chars)."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld9_test",
                email="fld9_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        value = "a" * 1024

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Long Field Value",
            fields=[{"name": "Name", "value": value}],
        )

        assert len(embed.fields[0].value) == 1024

    def test_field_value_exceeds_max_length(self, db, auth_manager):
        """Test field value exceeding max length fails."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld10_test",
                email="fld10_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        value = "a" * 1025

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Too Long Field Value",
                fields=[{"name": "Name", "value": value}],
            )

    def test_field_value_required(self, db, auth_manager):
        """Test field value is required."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld11_test",
                email="fld11_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        with pytest.raises(EmbedValidationError):
            embeds._manager.create_embed(
                user_id=user.id,
                title="Missing Field Value",
                fields=[{"name": "Name only"}],
            )


class TestFieldOrdering:
    """Tests for field ordering."""

    def test_fields_maintain_order(self, db, auth_manager):
        """Test fields maintain insertion order."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld12_test",
                email="fld12_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Ordered Fields",
            fields=[
                {"name": "First", "value": "1"},
                {"name": "Second", "value": "2"},
                {"name": "Third", "value": "3"},
            ],
        )

        assert embed.fields[0].name == "First"
        assert embed.fields[1].name == "Second"
        assert embed.fields[2].name == "Third"

    def test_fields_order_after_retrieval(self, db, auth_manager):
        """Test fields maintain order after retrieval."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld13_test",
                email="fld13_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        created = embeds._manager.create_embed(
            user_id=user.id,
            title="Order Test",
            fields=[
                {"name": "A", "value": "1"},
                {"name": "B", "value": "2"},
                {"name": "C", "value": "3"},
            ],
        )

        retrieved = embeds._manager.get_embed(created.id)

        assert retrieved.fields[0].name == "A"
        assert retrieved.fields[1].name == "B"
        assert retrieved.fields[2].name == "C"


class TestFieldInlineLayout:
    """Tests for inline field layout."""

    def test_mixed_inline_fields(self, db, auth_manager):
        """Test mixed inline and non-inline fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld14_test",
                email="fld14_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Mixed Inline",
            fields=[
                {"name": "Inline 1", "value": "V1", "inline": True},
                {"name": "Inline 2", "value": "V2", "inline": True},
                {"name": "Full Width", "value": "V3", "inline": False},
                {"name": "Inline 3", "value": "V4", "inline": True},
            ],
        )

        assert embed.fields[0].inline is True
        assert embed.fields[1].inline is True
        assert embed.fields[2].inline is False
        assert embed.fields[3].inline is True

    def test_all_inline_fields(self, db, auth_manager):
        """Test all inline fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld15_test",
                email="fld15_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="All Inline",
            fields=[
                {"name": f"Field {i}", "value": f"Value {i}", "inline": True}
                for i in range(6)
            ],
        )

        assert all(f.inline for f in embed.fields)

    def test_no_inline_fields(self, db, auth_manager):
        """Test no inline fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="fld16_test",
                email="fld16_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="No Inline",
            fields=[
                {"name": f"Field {i}", "value": f"Value {i}", "inline": False}
                for i in range(3)
            ],
        )

        assert all(not f.inline for f in embed.fields)
