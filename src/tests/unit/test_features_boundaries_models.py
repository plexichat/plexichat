"""Regression tests for feature-route request boundaries.

These are pure model/helper tests: they import the route modules directly and
never build the full API app, so a regression in an unrelated route cannot hide
a boundary regression here. Each guard was added during the feature/suite
hardening sweep; if a constant or validator is deleted, these fail.
"""

from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.routes.features import bookmarks, profiles, push, reports
from src.api.routes.features import scheduled_messages, tier_features, voice
from src.api.schemas.features import UpdateFeaturesRequest


def _invalid(model, **kwargs):
    with pytest.raises(ValidationError):
        model(**kwargs)


class TestBookmarkBoundaries:
    def test_label_is_trimmed_and_bounded(self):
        body = bookmarks.BookmarkRequest(
            message_id="1", conversation_id="2", label="  hello  ", url=None
        )
        assert body.label == "hello"
        _invalid(
            bookmarks.BookmarkRequest,
            message_id="1",
            conversation_id="2",
            label="x" * (bookmarks.MAX_LABEL_LENGTH + 1),
        )
        _invalid(
            bookmarks.BookmarkRequest,
            message_id="1",
            conversation_id="2",
            label="a\x00b",
        )

    @pytest.mark.parametrize(
        "url",
        ["javascript:alert(1)", "data:text/html,x", "ftp://x/y", "//x/y", "/relative"],
    )
    def test_deep_link_scheme_restricted(self, url):
        _invalid(
            bookmarks.BookmarkRequest,
            message_id="1",
            conversation_id="2",
            url=url,
        )

    def test_http_url_accepted(self):
        body = bookmarks.BookmarkRequest(
            message_id="1",
            conversation_id="2",
            label=None,
            url="https://example.com/message/1",
        )
        assert body.url == "https://example.com/message/1"


class TestFeatureSettingsBoundaries:
    def test_user_id_must_be_positive_snowflake(self):
        assert tier_features._feature_user_id("123") == 123
        for raw in ("-1", "0", "abc", "99999999999999999999999"):
            with pytest.raises(HTTPException) as exc:
                tier_features._feature_user_id(raw)
            assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "body",
        [
            UpdateFeaturesRequest(
                rate_limit_tier="x" * 51, expires_at=None, notes=None
            ),
            UpdateFeaturesRequest(
                rate_limit_tier="bad tier!", expires_at=None, notes=None
            ),
            UpdateFeaturesRequest(
                rate_limit_tier=None, expires_at=None, notes="a\x00b"
            ),
            UpdateFeaturesRequest(
                rate_limit_tier=None, expires_at=None, notes="n" * 2001
            ),
            UpdateFeaturesRequest(rate_limit_tier=None, expires_at=-1, notes=None),
            UpdateFeaturesRequest(rate_limit_tier=None, expires_at=10**12, notes=None),
        ],
    )
    def test_bad_settings_rejected(self, body):
        with pytest.raises(HTTPException) as exc:
            tier_features._validate_feature_settings(body)
        assert exc.value.status_code == 400


class TestVoiceBoundaries:
    def _body(self, **kwargs):
        base = {
            "conversation_id": "1",
            "duration_ms": 1000,
            "filename": "a.webm",
            "content_type": "audio/webm",
            "size": 10,
            "url": "/api/v1/media/x",
        }
        base.update(kwargs)
        return base

    def test_scope_ids_are_snowflakes(self):
        body = voice.VoiceMessageRequest(**self._body(channel_id="2", server_id="3"))
        assert body.channel_id == "2"
        _invalid(voice.VoiceMessageRequest, **self._body(channel_id="-1"))
        _invalid(voice.VoiceMessageRequest, **self._body(server_id="10" * 40))

    def test_session_fields_bounded(self):
        _invalid(voice.VoiceMessageRequest, **self._body(duration_ms=10**9))
        _invalid(voice.VoiceMessageRequest, **self._body(filename="a" * 256))
        _invalid(voice.VoiceMessageRequest, **self._body(waveform="x" * 9000))
        _invalid(voice.VoiceMessageRequest, **self._body(filename="a\x00b"))


class TestReportBoundaries:
    def _body(self, **kwargs) -> dict[str, Any]:
        base = {"target_type": "user", "target_id": "1", "reason": "why"}
        base.update(kwargs)
        return base

    def test_category_and_priority_are_enums(self):
        body = reports.EnhancedReportRequest(**self._body())
        assert body.category == "other"
        _invalid(reports.EnhancedReportRequest, **self._body(category="bogus"))
        _invalid(reports.EnhancedReportRequest, **self._body(priority="urgent"))
        _invalid(reports.EnhancedReportRequest, **self._body(target_type="bogus"))

    def test_reason_details_and_evidence_bounded(self):
        _invalid(reports.EnhancedReportRequest, **self._body(reason="x" * 1001))
        _invalid(reports.EnhancedReportRequest, **self._body(details="a\x00b"))
        _invalid(
            reports.EnhancedReportRequest,
            **self._body(evidence_urls=["javascript:alert(1)"]),
        )
        _invalid(
            reports.EnhancedReportRequest,
            **self._body(evidence_urls=["https://x"] * 6),
        )

    def test_status_update_bounded(self):
        _invalid(reports.ReportStatusUpdateRequest, status="bogus")
        _invalid(
            reports.ReportStatusUpdateRequest, status="open", resolution="x" * 2001
        )
        _invalid(reports.ReportStatusUpdateRequest, status="open", assigned_to="-1")


class TestProfileBoundaries:
    def test_emoji_and_social_links_validated(self):
        body = profiles.ProfileUpdateRequest(
            bio=None,
            status=None,
            status_emoji="🔥",
            pronouns=None,
            location=None,
            timezone=None,
            banner_url=None,
            social_links=[{"platform": "x", "url": "https://example.com/u"}],
        )
        assert body.status_emoji == "🔥"
        _invalid(profiles.ProfileUpdateRequest, status_emoji="<img>")
        _invalid(profiles.ProfileUpdateRequest, status_emoji="🔥" * 9)
        _invalid(
            profiles.ProfileUpdateRequest,
            social_links=[{"url": "javascript:alert(1)"}],
        )
        _invalid(profiles.ProfileUpdateRequest, social_links=[{}] * 11)
        _invalid(profiles.ProfileUpdateRequest, bio="a\x00b")


class TestPushBoundaries:
    def test_token_fields_bounded(self):
        base = {"token": "t", "platform": "ios"}
        push.PushTokenRequest(**base)
        _invalid(push.PushTokenRequest, **{**base, "token": "x" * 2049})
        _invalid(push.PushTokenRequest, **{**base, "platform": "opaque"})
        _invalid(push.PushTokenRequest, **{**base, "device_id": "a\x00b"})
        _invalid(push.PushTokenRequest, **{**base, "p256dh": "x" * 257})


class TestScheduledMessageBoundaries:
    def test_content_and_schedule_window_bounded(self):
        base = {"conversation_id": "1", "content": "hi", "scheduled_at": 1}
        scheduled_messages.ScheduledMessageRequest(**base)
        _invalid(scheduled_messages.ScheduledMessageRequest, **{**base, "content": " "})
        _invalid(
            scheduled_messages.ScheduledMessageRequest, **{**base, "scheduled_at": 0}
        )
        _invalid(
            scheduled_messages.ScheduledMessageRequest,
            **{**base, "scheduled_at": 10**14},
        )
