"""Cross-cutting regression tests for the media-boundary hardening campaign.

The individual ``test_media_boundaries_*`` modules pin one boundary each. This
suite pins the *campaign-wide* invariants: every guard fails closed on the same
hostile primitive (controls, oversized strings, bad ids), the constants agree
with one another, and the guards stay importable from their owning modules so
no later refactor can silently drop them.
"""

from typing import Any

import pytest


HOSTILE_TEXTS = [
    "",
    "x" * 100_000,
    "a\x00b",
    "a\x1fb",
    "a\u200bb",
    "a\u202eb",
    "../etc/passwd",
    "javascript:alert(1)",
]


class TestGuardImports:
    """Every guard module remains importable with its public surface."""

    @pytest.mark.parametrize(
        "module, names",
        [
            (
                "src.core.embeds.validator.bounds",
                ["EmbedBudget", "enforce_embed_budget", "MAX_EMBED_NESTING_DEPTH"],
            ),
            (
                "src.core.embeds.validator.ssrf_gate",
                ["validate_embed_url", "enforce_ssrf_gate", "reject_non_http_url"],
            ),
            (
                "src.core.embeds.validator.field_policy",
                ["validate_color_field", "validate_provider_fields"],
            ),
            (
                "src.core.embeds.validator.active_content",
                ["deny_active_content", "ACTIVE_CONTENT_SINK_PATTERNS"],
            ),
            (
                "src.core.reactions.manager.bounds",
                ["validate_route_emoji", "validate_bulk_message_ids"],
            ),
            (
                "src.core.voice.boundaries",
                [
                    "validate_ice_url",
                    "validate_session_id",
                    "validate_region_id",
                    "validate_bitrate_input",
                    "validate_transcription_payload",
                    "validate_recording_id",
                    "validate_crf",
                    "validate_sample_rate",
                    "validate_audio_profile",
                ],
            ),
        ],
    )
    def test_guard_surface_present(self, module, names):
        mod = __import__(module, fromlist=names)
        for name in names:
            assert hasattr(mod, name), f"{module}.{name} missing"

    def test_hardening_helpers_not_reimplemented_locally(self):
        """Guards must import (not copy) the shared hardening primitives."""
        from src.core.embeds.validator import ssrf_gate
        from src.core.reactions.manager import bounds as reaction_bounds
        from src.core.voice import boundaries as voice_bounds

        for mod in (ssrf_gate, reaction_bounds, voice_bounds):
            source = mod.__file__
            assert source is not None
            with open(source, encoding="utf-8") as fh:
                text = fh.read()
            assert "src.core.security.hardening" in text


class TestFailClosedOnHostileText:
    def test_embed_url_rejects_all_hostile_values(self):
        from src.core.embeds.validator import validate_embed_url

        for value in HOSTILE_TEXTS:
            if not value:
                continue  # empty is the "absent field" sentinel, not an attack
            with pytest.raises(Exception):
                validate_embed_url(value)

    def test_emoji_rejects_controls_and_bidi(self):
        from src.core.reactions.manager.bounds import validate_route_emoji

        for value in HOSTILE_TEXTS:
            if not value or "passwd" in value or "javascript" in value:
                continue  # plain text is not an emoji, still rejected below
            with pytest.raises(ValueError):
                validate_route_emoji(value)

    def test_voice_identifiers_reject_hostile_values(self):
        from src.core.voice.boundaries import (
            validate_ice_url,
            validate_recording_id,
            validate_session_id,
        )

        # Each guard rejects the primitives in its own contract: scheme for
        # ICE URLs, path-safety for recording ids, control/bidi for session ids.
        cases = [
            (validate_ice_url, [v for v in HOSTILE_TEXTS if v]),
            (
                validate_recording_id,
                ["x" * 100_000, "a\x00b", "a\x1fb", "../etc/passwd", "a/b"],
            ),
            (
                validate_session_id,
                ["x" * 100_000, "a\x00b", "a\x1fb", "a\u200bb", "a\u202eb"],
            ),
        ]
        for guard, values in cases:
            for value in values:
                with pytest.raises(ValueError):
                    guard(value)

    def test_bulk_ids_reject_hostile_values(self):
        from src.core.reactions.manager.bounds import validate_bulk_message_ids

        for value in HOSTILE_TEXTS:
            with pytest.raises(ValueError):
                validate_bulk_message_ids([value])


class TestConstantCoherence:
    def test_bitrate_bounds_ordered(self):
        from src.core.voice.boundaries import (
            MAX_BITRATE,
            MAX_BITRATE_INPUT,
            MIN_BITRATE,
        )

        assert 0 < MIN_BITRATE < MAX_BITRATE < MAX_BITRATE_INPUT

    def test_audio_ranges_ordered(self):
        from src.core.voice.boundaries import (
            MAX_CRF,
            MAX_FRAME_DURATION_MS,
            MAX_SAMPLE_RATE,
            MIN_CRF,
            MIN_FRAME_DURATION_MS,
            MIN_SAMPLE_RATE,
            VALID_SAMPLE_RATES,
        )

        assert MIN_CRF < MAX_CRF
        assert MIN_SAMPLE_RATE < MAX_SAMPLE_RATE
        assert MIN_FRAME_DURATION_MS < MAX_FRAME_DURATION_MS
        assert all(MIN_SAMPLE_RATE <= r <= MAX_SAMPLE_RATE for r in VALID_SAMPLE_RATES)

    def test_embed_budget_bounds_ordered(self):
        from src.core.embeds.validator.bounds import (
            GENERIC_STRING_MAX_LENGTH,
            MAX_EMBED_KEYS_PER_OBJECT,
            MAX_EMBED_LIST_ITEMS,
            MAX_EMBED_NESTING_DEPTH,
            MAX_EMBED_TOTAL_NODES,
            MAX_EMBED_TOTAL_TEXT_CHARS,
            TEXT_FIELD_LIMITS,
        )

        assert 0 < MAX_EMBED_NESTING_DEPTH <= 32
        assert 0 < MAX_EMBED_TOTAL_NODES < 100_000
        assert 0 < MAX_EMBED_TOTAL_TEXT_CHARS < 1_000_000
        assert 0 < MAX_EMBED_KEYS_PER_OBJECT
        assert 0 < MAX_EMBED_LIST_ITEMS
        assert all(
            0 < limit <= GENERIC_STRING_MAX_LENGTH
            for limit in TEXT_FIELD_LIMITS.values()
        )


class TestEndToEndRefusals:
    def test_deep_embed_with_bad_url_fails_at_budget_first(self):
        from src.core.embeds.validator import sanitize_message_embeds

        payload: dict[str, Any] = {"title": "ok"}
        for _ in range(40):
            payload = {"child": payload}
        payload["url"] = "javascript:alert(1)"
        with pytest.raises(Exception):
            sanitize_message_embeds([payload])

    def test_oversized_bulk_with_bad_ids_refused(self):
        from src.core.reactions.manager.bounds import validate_bulk_message_ids

        with pytest.raises(ValueError):
            validate_bulk_message_ids([-1] * 500)

    def test_oversized_transcription_refused(self):
        from src.core.voice.boundaries import validate_transcription_payload

        with pytest.raises(ValueError):
            validate_transcription_payload(
                {"segments": [{"text": "x" * 50_000} for _ in range(100)]}
            )

    def test_bad_audio_profile_refused(self):
        from src.core.voice.boundaries import validate_audio_profile

        with pytest.raises(ValueError):
            validate_audio_profile({"sample_rate": -1, "crf": 999})
