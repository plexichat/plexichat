"""Color and provider fields are length-bounded and free of active content."""

import pytest

from src.core.embeds.exceptions import (
    EmbedSanitizationError,
    EmbedValidationError,
    InvalidColorError,
)
from src.core.embeds.validator import (
    MAX_COLOR_FIELD_LENGTH,
    MAX_PROVIDER_NAME_LENGTH,
    deny_active_content,
    sanitize_message_embeds,
    validate_color_field,
    validate_embed_data,
    validate_provider_fields,
)


class TestActiveContentDenylist:
    @pytest.mark.parametrize(
        "value",
        [
            "<script>alert(1)</script>",
            "<svg onload=alert(1)>",
            'x srcdoc="<script>"',
            "x formaction=javascript:alert(1)",
            "url(javascript:alert(1))",
            "expression(alert(1))",
            "@import url(evil.css)",
            "onclick=alert(1)",
            "data:text/html,x",
            "line\x00break",
        ],
    )
    def test_sinks_rejected(self, value):
        with pytest.raises(EmbedSanitizationError):
            deny_active_content(value, "provider.name")

    def test_plain_text_passes(self):
        assert (
            deny_active_content("Example Provider", "provider.name")
            == "Example Provider"
        )


class TestColorField:
    @pytest.mark.parametrize("value", ["#FF0000", "FF0000", "#abc", "5865F2"])
    def test_valid_colors(self, value):
        result = validate_color_field(value)
        assert result.startswith("#")

    @pytest.mark.parametrize(
        "value",
        ["url(javascript:alert(1))", "<script>", "a" * (MAX_COLOR_FIELD_LENGTH + 1)],
    )
    def test_unsafe_or_oversized_rejected(self, value):
        with pytest.raises(InvalidColorError):
            validate_color_field(value)

    def test_message_sanitizer_rejects_css_sink(self):
        with pytest.raises(InvalidColorError):
            sanitize_message_embeds([{"title": "x", "color": "expression(alert(1))"}])


class TestProviderFields:
    def test_valid_provider(self):
        result = validate_provider_fields(
            {"name": "Example", "url": "https://example.com"}
        )
        assert result["name"] == "Example"

    def test_non_object_rejected(self):
        with pytest.raises(EmbedValidationError, match="must be an object"):
            validate_provider_fields("oops")

    def test_oversized_name_rejected(self):
        with pytest.raises(EmbedValidationError, match="maximum length"):
            validate_provider_fields({"name": "a" * (MAX_PROVIDER_NAME_LENGTH + 1)})

    @pytest.mark.parametrize(
        "name", ["<script>alert(1)</script>", "x srcdoc=evil", "url(javascript:x)"]
    )
    def test_active_content_name_rejected(self, name):
        with pytest.raises(EmbedSanitizationError):
            validate_provider_fields({"name": name})

    def test_provider_url_still_passes_url_gate(self):
        with pytest.raises(Exception):
            validate_provider_fields({"url": "javascript:alert(1)"})

    def test_embed_data_provider_is_validated(self):
        result = validate_embed_data({"provider": {"name": "a" * 500}})
        assert result.valid is False
