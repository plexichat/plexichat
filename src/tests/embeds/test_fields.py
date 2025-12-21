"""
Tests for embed fields (name, value, inline).
"""

import pytest
from src.core.embeds import EmbedValidationError


class TestCreateEmbedWithFields:
    """Tests for creating embeds with fields."""

    def test_create_embed_with_single_field(self, db_and_modules):
        """Test creating embed with single field."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld1_{unique_id}",
            email=f"fld1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Field Test",
            fields=[{"name": "Field Name", "value": "Field Value"}]
        )

        assert len(embed.fields) == 1
        assert embed.fields[0].name == "Field Name"
        assert embed.fields[0].value == "Field Value"
        assert embed.fields[0].inline is False

    def test_create_embed_with_inline_field(self, db_and_modules):
        """Test creating embed with inline field."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld2_{unique_id}",
            email=f"fld2_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Inline Field Test",
            fields=[{"name": "Inline Field", "value": "Inline Value", "inline": True}]
        )

        assert embed.fields[0].inline is True

    def test_create_embed_with_multiple_fields(self, db_and_modules):
        """Test creating embed with multiple fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld3_{unique_id}",
            email=f"fld3_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Multiple Fields",
            fields=[
                {"name": "Field 1", "value": "Value 1", "inline": True},
                {"name": "Field 2", "value": "Value 2", "inline": True},
                {"name": "Field 3", "value": "Value 3", "inline": False}
            ]
        )

        assert len(embed.fields) == 3
        assert embed.fields[0].name == "Field 1"
        assert embed.fields[1].name == "Field 2"
        assert embed.fields[2].name == "Field 3"

    def test_create_embed_with_max_fields(self, db_and_modules):
        """Test creating embed with maximum 25 fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld4_{unique_id}",
            email=f"fld4_{unique_id}@example.com",
            password="TestPass123!"
        )

        fields = [{"name": f"Field {i}", "value": f"Value {i}"} for i in range(25)]

        embed = embeds.create_embed(
            user_id=user.id,
            title="Max Fields",
            fields=fields
        )

        assert len(embed.fields) == 25

    def test_create_embed_exceeds_max_fields(self, db_and_modules):
        """Test creating embed with more than 25 fields fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld5_{unique_id}",
            email=f"fld5_{unique_id}@example.com",
            password="TestPass123!"
        )

        fields = [{"name": f"Field {i}", "value": f"Value {i}"} for i in range(26)]

        with pytest.raises(EmbedValidationError) as exc_info:
            embeds.create_embed(
                user_id=user.id,
                title="Too Many Fields",
                fields=fields
            )

        assert any("25" in issue or "field" in issue.lower() for issue in exc_info.value.issues)


class TestFieldNameValidation:
    """Tests for field name validation."""

    def test_field_name_max_length(self, db_and_modules):
        """Test field name at max length (256 chars)."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld6_{unique_id}",
            email=f"fld6_{unique_id}@example.com",
            password="TestPass123!"
        )

        name = "a" * 256

        embed = embeds.create_embed(
            user_id=user.id,
            title="Long Field Name",
            fields=[{"name": name, "value": "Value"}]
        )

        assert len(embed.fields[0].name) == 256

    def test_field_name_exceeds_max_length(self, db_and_modules):
        """Test field name exceeding max length fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld7_{unique_id}",
            email=f"fld7_{unique_id}@example.com",
            password="TestPass123!"
        )

        name = "a" * 257

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Too Long Field Name",
                fields=[{"name": name, "value": "Value"}]
            )

    def test_field_name_required(self, db_and_modules):
        """Test field name is required."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld8_{unique_id}",
            email=f"fld8_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Missing Field Name",
                fields=[{"value": "Value only"}]
            )


class TestFieldValueValidation:
    """Tests for field value validation."""

    def test_field_value_max_length(self, db_and_modules):
        """Test field value at max length (1024 chars)."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld9_{unique_id}",
            email=f"fld9_{unique_id}@example.com",
            password="TestPass123!"
        )

        value = "a" * 1024

        embed = embeds.create_embed(
            user_id=user.id,
            title="Long Field Value",
            fields=[{"name": "Name", "value": value}]
        )

        assert len(embed.fields[0].value) == 1024

    def test_field_value_exceeds_max_length(self, db_and_modules):
        """Test field value exceeding max length fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld10_{unique_id}",
            email=f"fld10_{unique_id}@example.com",
            password="TestPass123!"
        )

        value = "a" * 1025

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Too Long Field Value",
                fields=[{"name": "Name", "value": value}]
            )

    def test_field_value_required(self, db_and_modules):
        """Test field value is required."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld11_{unique_id}",
            email=f"fld11_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedValidationError):
            embeds.create_embed(
                user_id=user.id,
                title="Missing Field Value",
                fields=[{"name": "Name only"}]
            )


class TestFieldOrdering:
    """Tests for field ordering."""

    def test_fields_maintain_order(self, db_and_modules):
        """Test fields maintain insertion order."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld12_{unique_id}",
            email=f"fld12_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Ordered Fields",
            fields=[
                {"name": "First", "value": "1"},
                {"name": "Second", "value": "2"},
                {"name": "Third", "value": "3"}
            ]
        )

        assert embed.fields[0].name == "First"
        assert embed.fields[1].name == "Second"
        assert embed.fields[2].name == "Third"

    def test_fields_order_after_retrieval(self, db_and_modules):
        """Test fields maintain order after retrieval."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld13_{unique_id}",
            email=f"fld13_{unique_id}@example.com",
            password="TestPass123!"
        )

        created = embeds.create_embed(
            user_id=user.id,
            title="Order Test",
            fields=[
                {"name": "A", "value": "1"},
                {"name": "B", "value": "2"},
                {"name": "C", "value": "3"}
            ]
        )

        retrieved = embeds.get_embed(created.id)

        assert retrieved.fields[0].name == "A"
        assert retrieved.fields[1].name == "B"
        assert retrieved.fields[2].name == "C"


class TestFieldInlineLayout:
    """Tests for inline field layout."""

    def test_mixed_inline_fields(self, db_and_modules):
        """Test mixed inline and non-inline fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld14_{unique_id}",
            email=f"fld14_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Mixed Inline",
            fields=[
                {"name": "Inline 1", "value": "V1", "inline": True},
                {"name": "Inline 2", "value": "V2", "inline": True},
                {"name": "Full Width", "value": "V3", "inline": False},
                {"name": "Inline 3", "value": "V4", "inline": True}
            ]
        )

        assert embed.fields[0].inline is True
        assert embed.fields[1].inline is True
        assert embed.fields[2].inline is False
        assert embed.fields[3].inline is True

    def test_all_inline_fields(self, db_and_modules):
        """Test all inline fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld15_{unique_id}",
            email=f"fld15_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="All Inline",
            fields=[
                {"name": f"Field {i}", "value": f"Value {i}", "inline": True}
                for i in range(6)
            ]
        )

        assert all(f.inline for f in embed.fields)

    def test_no_inline_fields(self, db_and_modules):
        """Test no inline fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"fld16_{unique_id}",
            email=f"fld16_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="No Inline",
            fields=[
                {"name": f"Field {i}", "value": f"Value {i}", "inline": False}
                for i in range(3)
            ]
        )

        assert all(not f.inline for f in embed.fields)
