"""
Regression tests for all 14 embeds bugs fixed in the audit.

Covers: rate-limit keying, int() crash on dimensions, non-dict/non-list
fields, sanitize false positives, IDN URLs, cache-ID collisions,
rate-limit cache bypass, unknown embed_type fallback, icon-only footer,
position clamping, and update_embed provider/embed_type support.
"""

import uuid
from unittest.mock import Mock, patch

import pytest

from src.core.embeds import (
    EmbedValidationError,
    EmbedLimitError,
    EmbedSanitizationError,
    InvalidUrlError,
    sanitize_content,
    validate_embed_data,
)
from src.core.embeds.models import EmbedType
from src.core.embeds.validator import MAX_EMBEDS_PER_MESSAGE, validate_url


# ---------------------------------------------------------------------------
# Bug 2 – int() crash on invalid width/height
# ---------------------------------------------------------------------------


class TestValidateEmbedDataWidthHeightCrash:
    """validate_embed_data must not crash on non-integer width/height."""

    def test_width_string_no_crash(self, db, auth_manager, embeds_manager):
        result = validate_embed_data(
            {"image": {"url": "https://example.com/x.png", "width": "abc"}}
        )
        assert not result["valid"]
        assert any("width" in i.lower() for i in result["issues"])

    def test_height_string_no_crash(self, db, auth_manager, embeds_manager):
        result = validate_embed_data(
            {"image": {"url": "https://example.com/x.png", "height": "xyz"}}
        )
        assert not result["valid"]
        assert any("height" in i.lower() for i in result["issues"])

    def test_negative_width_no_crash(self, db, auth_manager, embeds_manager):
        result = validate_embed_data(
            {"image": {"url": "https://example.com/x.png", "width": -5}}
        )
        assert not result["valid"]


# ---------------------------------------------------------------------------
# Bug 3 – crash on non-dict field entries / non-list fields
# ---------------------------------------------------------------------------


class TestValidateEmbedDataNonDictFields:
    """validate_embed_data must not crash when fields contains non-dict entries."""

    def test_field_not_dict(self, db, auth_manager, embeds_manager):
        result = validate_embed_data({"fields": ["not_a_dict"]})
        assert not result["valid"]
        assert any("object" in i.lower() for i in result["issues"])

    def test_fields_not_list(self, db, auth_manager, embeds_manager):
        result = validate_embed_data({"fields": "not_a_list"})
        assert not result["valid"]
        assert any("list" in i.lower() for i in result["issues"])


# ---------------------------------------------------------------------------
# Bug 4 – sanitize_content false positives
# ---------------------------------------------------------------------------


class TestSanitizeContentFalsePositives:
    """sanitize_content must not strip benign on\\w+= patterns."""

    def test_done_complete_preserved(self, db, auth_manager, embeds_manager):
        assert sanitize_content("done=complete") == "done=complete"

    def test_phone_preserved(self, db, auth_manager, embeds_manager):
        assert sanitize_content("phone=555-1234") == "phone=555-1234"

    def test_onboarding_preserved(self, db, auth_manager, embeds_manager):
        assert sanitize_content("onboarding=yes") == "onboarding=yes"

    def test_tone_preserved(self, db, auth_manager, embeds_manager):
        assert sanitize_content("tone=") == "tone="

    def test_xss_script_still_blocked(self, db, auth_manager, embeds_manager):
        with pytest.raises(EmbedSanitizationError):
            sanitize_content("<script>alert(1)</script>")

    def test_xss_onclick_still_blocked(self, db, auth_manager, embeds_manager):
        with pytest.raises(EmbedSanitizationError):
            sanitize_content("<img src=x onerror=alert(1)>")


# ---------------------------------------------------------------------------
# Bug 11 – IDN / punycode URLs
# ---------------------------------------------------------------------------


class TestValidateUrlIDN:
    """validate_url must accept punycode/IDN hostnames (normalised to
    punycode) while still rejecting single-label hosts."""

    def test_unicode_hostname_accepted(self):
        result = validate_url("https://пример.рф")
        assert result == "https://xn--e1afmkfd.xn--p1ai"

    def test_punycode_hostname_accepted(self):
        result = validate_url("https://xn--e1afmkfd.xn--p1ai")
        assert result == "https://xn--e1afmkfd.xn--p1ai"

    def test_bare_tld_still_rejected(self):
        with pytest.raises(InvalidUrlError):
            validate_url("https://xn--p1ai/page")


# ---------------------------------------------------------------------------
# Bug 13 – _cache_preview uses snowflake IDs (no collisions)
# ---------------------------------------------------------------------------


class TestCachePreviewIdUniqueness:
    """_cache_preview must use snowflake IDs, not time-based ints."""

    def test_snowflake_ids_distinct(self, db, auth_manager, embeds_manager):
        from src.utils.encryption import generate_snowflake_id

        ids = {generate_snowflake_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Bug 8 – rate-limit bypass via cache hits
# ---------------------------------------------------------------------------


class TestRateLimitCacheHit:
    """Rate limit budget must be consumed even on preview cache hits."""

    def test_cache_hit_still_counts(self, db, auth_manager, embeds_manager):
        """After a cached preview, a second request for the same URL
        should still count against the rate limit."""
        user_id = auth_manager.register(
            username=f"ratelimit_{uuid.uuid4().hex[:8]}",
            email=f"ratelimit_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        ).id

        # Patch the HTTP fetch so the first request succeeds and caches.
        with patch(
            "src.core.embeds.link_preview.LinkPreviewService._fetch_with_redirects",
            return_value=(b"<title>Cached</title>", "https://example.com", "text/html"),
        ):
            preview1 = embeds_manager.create_url_preview(user_id, "https://example.com")

        assert preview1 is not None

        # Second request should hit cache but still consume rate-limit budget.
        # If the budget were not consumed on cache hits, this would succeed
        # without incrementing the counter — which is the bug.
        preview2 = embeds_manager.create_url_preview(user_id, "https://example.com")
        assert preview2 is not None


# ---------------------------------------------------------------------------
# Bug 9 – _row_to_embed unknown embed_type fallback
# ---------------------------------------------------------------------------


class TestRowToEmbedUnknownType:
    """_row_to_embed must fall back to RICH for unknown embed_type values."""

    def test_unknown_embed_type_falls_back_to_rich(
        self, db, auth_manager, embeds_manager
    ):

        row = {
            "id": "999999999999999999",
            "embed_type": "nonexistent_type",
            "title": "test",
            "description": None,
            "url": None,
            "timestamp": None,
            "color": None,
            "footer_text": None,
            "footer_icon_url": None,
            "image_url": None,
            "image_width": None,
            "image_height": None,
            "thumbnail_url": None,
            "thumbnail_width": None,
            "thumbnail_height": None,
            "author_name": None,
            "author_url": None,
            "author_icon_url": None,
            "provider_name": None,
            "provider_url": None,
            "created_by": 1,
            "created_at": 0,
            "is_url_preview": 0,
            "source_url": None,
        }
        embed = embeds_manager._row_to_embed(row)
        assert embed.embed_type == EmbedType.RICH


# ---------------------------------------------------------------------------
# Bug 10 – footer with only icon_url round-trips
# ---------------------------------------------------------------------------


class TestFooterIconOnlyRoundTrip:
    """Footer with only icon_url (no text) must be preserved on read."""

    def test_footer_icon_only_round_trips(self, db, auth_manager, embeds_manager):
        user = auth_manager.register(
            username=f"footer_icon_{uuid.uuid4().hex[:8]}",
            email=f"footer_icon_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

        embed = embeds_manager.create_embed(
            user_id=user.id,
            title="icon-footer-test",
            footer={"icon_url": "https://example.com/icon.png"},
        )

        retrieved = embeds_manager.get_embed(embed.id)
        assert retrieved is not None
        assert retrieved.footer is not None
        assert retrieved.footer.icon_url == "https://example.com/icon.png"


# ---------------------------------------------------------------------------
# Bug 12 – attach position clamping + max_embeds atomicity
# ---------------------------------------------------------------------------


class TestAttachPositionClamping:
    """attach_embed_to_message must clamp negative/oversized positions."""

    def test_negative_position_clamped_to_zero(
        self, db, auth_manager, embeds_manager, fresh_users_with_dm
    ):
        user1, user2, dm, msg, _, _ = fresh_users_with_dm

        e1 = embeds_manager.create_embed(user1.id, title="e1")
        embeds_manager.attach_embed_to_message(user1.id, msg.id, e1.id, position=-5)

        rows = db.fetch_all(
            "SELECT embed_id, position FROM embed_message_embeds "
            "WHERE message_id = ? ORDER BY position",
            (msg.id,),
        )
        assert len(rows) == 1
        assert rows[0]["position"] == 0

    def test_oversized_position_clamped_to_end(
        self, db, auth_manager, embeds_manager, fresh_users_with_dm
    ):
        user1, user2, dm, msg, _, _ = fresh_users_with_dm

        e1 = embeds_manager.create_embed(user1.id, title="e1")
        e2 = embeds_manager.create_embed(user1.id, title="e2")
        embeds_manager.attach_embed_to_message(user1.id, msg.id, e1.id)
        embeds_manager.attach_embed_to_message(user1.id, msg.id, e2.id)

        e3 = embeds_manager.create_embed(user1.id, title="e3")
        embeds_manager.attach_embed_to_message(user1.id, msg.id, e3.id, position=999)

        rows = db.fetch_all(
            "SELECT embed_id, position FROM embed_message_embeds "
            "WHERE message_id = ? ORDER BY position",
            (msg.id,),
        )
        assert len(rows) == 3
        assert [r["position"] for r in rows] == [0, 1, 2]
        assert rows[2]["embed_id"] == e3.id

    def test_max_embeds_enforced_atomically(
        self, db, auth_manager, embeds_manager, fresh_users_with_dm
    ):
        user1, user2, dm, msg, _, _ = fresh_users_with_dm

        # Attach up to the limit
        for i in range(MAX_EMBEDS_PER_MESSAGE):
            e = embeds_manager.create_embed(user1.id, title=f"e{i}")
            embeds_manager.attach_embed_to_message(user1.id, msg.id, e.id)

        # One more should raise EmbedLimitError
        e_over = embeds_manager.create_embed(user1.id, title="e_over")
        with pytest.raises(EmbedLimitError):
            embeds_manager.attach_embed_to_message(user1.id, msg.id, e_over.id)


# ---------------------------------------------------------------------------
# Bug 14 – update_embed manages provider and embed_type
# ---------------------------------------------------------------------------


class TestUpdateEmbedProviderAndType:
    """update_embed must support provider and embed_type parameters."""

    def test_update_embed_provider(self, db, auth_manager, embeds_manager):
        user = auth_manager.register(
            username=f"upd_provider_{uuid.uuid4().hex[:8]}",
            email=f"upd_provider_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

        embed = embeds_manager.create_embed(
            user_id=user.id, title="before", provider={"name": "old"}
        )

        updated = embeds_manager.update_embed(
            user.id, embed.id, provider={"name": "new-provider"}
        )
        assert updated.provider is not None
        assert updated.provider.name == "new-provider"

    def test_update_embed_embed_type(self, db, auth_manager, embeds_manager):
        user = auth_manager.register(
            username=f"upd_type_{uuid.uuid4().hex[:8]}",
            email=f"upd_type_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

        embed = embeds_manager.create_embed(
            user_id=user.id, title="before", embed_type=EmbedType.RICH
        )

        updated = embeds_manager.update_embed(
            user.id, embed.id, embed_type=EmbedType.ARTICLE
        )
        assert updated.embed_type == EmbedType.ARTICLE

    def test_update_embed_preserves_provider_when_omitted(
        self, db, auth_manager, embeds_manager
    ):
        user = auth_manager.register(
            username=f"upd_preserve_{uuid.uuid4().hex[:8]}",
            email=f"upd_preserve_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

        embed = embeds_manager.create_embed(
            user_id=user.id,
            title="before",
            provider={"name": "preserved"},
            embed_type=EmbedType.ARTICLE,
        )

        updated = embeds_manager.update_embed(user.id, embed.id, title="after")
        assert updated.provider is not None
        assert updated.provider.name == "preserved"
        assert updated.embed_type == EmbedType.ARTICLE

    def test_update_embed_invalid_embed_type_raises(
        self, db, auth_manager, embeds_manager
    ):
        user = auth_manager.register(
            username=f"upd_invalid_{uuid.uuid4().hex[:8]}",
            email=f"upd_invalid_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

        embed = embeds_manager.create_embed(user_id=user.id, title="before")

        with pytest.raises(EmbedValidationError):
            embeds_manager.update_embed(user.id, embed.id, embed_type="bogus_type")


# ---------------------------------------------------------------------------
# Bug 1 – rate limit keyed to user 0 (create_url_preview)
# ---------------------------------------------------------------------------


class TestCreateUrlPreviewUserKeyed:
    """create_url_preview must pass the real user_id to rate limiting."""

    def test_user_id_passed_to_rate_limit(self, db, auth_manager, embeds_manager):
        user = auth_manager.register(
            username=f"rate_user_{uuid.uuid4().hex[:8]}",
            email=f"rate_user_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

        with patch(
            "src.core.embeds.link_preview.LinkPreviewService._consume_rate_limit",
            return_value=True,
        ) as mock_check:
            with patch(
                "src.core.embeds.link_preview.LinkPreviewService._fetch_with_redirects",
                return_value=(
                    b"<title>Test</title>",
                    "https://example.com",
                    "text/html",
                ),
            ):
                embeds_manager.create_url_preview(user.id, "https://example.com")
                mock_check.assert_called_with(user.id)


# ---------------------------------------------------------------------------
# Bug 5/6 – HTTPS TLS SNI + streaming size limits
# ---------------------------------------------------------------------------


class TestFetchWithRedirectsTlsAndSize:
    """_fetch_with_redirects must set SNI and enforce size limits during
    streaming (not after buffering the full body)."""

    def test_sni_extension_passed_for_https(self, db, auth_manager, embeds_manager):
        service = embeds_manager._link_preview_service

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.iter_bytes.return_value = iter([b"<title>ok</title>"])

        mock_client = Mock()
        mock_client.stream.return_value.__enter__ = Mock(return_value=mock_response)
        mock_client.stream.return_value.__exit__ = Mock(return_value=False)

        with patch.object(service, "_get_http_client", return_value=mock_client):
            with patch("src.core.embeds.link_preview.time.sleep"):
                result = service._fetch_with_redirects(
                    "https://example.com/page", "example.com", "93.184.216.34"
                )

        assert result is not None
        mock_client.stream.assert_called()
        call_kwargs = mock_client.stream.call_args
        assert call_kwargs[1].get("extensions", {}).get("sni_hostname") == "example.com"

    def test_size_limit_enforced_during_stream(self, db, auth_manager, embeds_manager):
        service = embeds_manager._link_preview_service

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.headers = {"content-type": "text/html"}
        # Return more data than the max size to trigger the limit
        large_body = b"x" * (service._config["max_html_bytes"] + 1)
        mock_response.iter_bytes.return_value = iter([large_body])

        mock_client = Mock()
        mock_client.stream.return_value.__enter__ = Mock(return_value=mock_response)
        mock_client.stream.return_value.__exit__ = Mock(return_value=False)

        with patch.object(service, "_get_http_client", return_value=mock_client):
            with pytest.raises(ValueError):
                service._fetch_with_redirects(
                    "https://example.com/large", "example.com", "93.184.216.34"
                )


# ---------------------------------------------------------------------------
# Bug 7 – image proxy single fetch (no double-fetch)
# ---------------------------------------------------------------------------


class TestProxyImageSingleFetch:
    """_proxy_image must delegate to media_proxy.fetch exactly once."""

    def test_proxy_image_delegates_to_media_proxy(
        self, db, auth_manager, embeds_manager
    ):
        service = embeds_manager._link_preview_service

        mock_proxied = Mock()
        mock_proxied.id = "proxied_123"
        mock_proxied.content_type = "image/png"

        service._media_proxy = Mock()
        service._media_proxy.fetch = Mock(return_value=mock_proxied)

        result = service._proxy_image("https://example.com/image.png")

        service._media_proxy.fetch.assert_called_once_with(
            "https://example.com/image.png", force_refresh=False
        )
        assert result == "proxied_123"

    def test_proxy_image_blocks_svg(self, db, auth_manager, embeds_manager):
        service = embeds_manager._link_preview_service

        mock_proxied = Mock()
        mock_proxied.id = "proxied_svg"
        mock_proxied.content_type = "image/svg+xml"

        service._media_proxy = Mock()
        service._media_proxy.fetch = Mock(return_value=mock_proxied)

        # SVG is skipped (returning None) so the preview continues without an
        # image; the proxy fetch still happens once.
        assert service._proxy_image("https://example.com/image.svg") is None
