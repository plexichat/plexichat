"""
Tests for webhook Ed25519 payload signing (outbound) and verification
with replay protection (inbound).
"""

import time

import pytest

from src.core.webhooks.manager.signature import WEBHOOK_REPLAY_WINDOW_SECONDS


@pytest.fixture
def signed(webhook_with_token):
    """A webhook plus a freshly signed payload."""
    setup = webhook_with_token
    payload = {"event": "message.created", "channel_id": 42}
    headers = setup["webhooks"].sign_payload(setup["webhook"].id, payload)
    return setup, payload, headers


class TestSignPayload:
    def test_sign_returns_versioned_headers(self, signed):
        setup, _payload, headers = signed
        assert "X-Plexichat-Signature" in headers
        assert "X-Plexichat-Timestamp" in headers
        timestamp = int(headers["X-Plexichat-Timestamp"])
        assert abs(timestamp - int(time.time() * 1000)) < 5000

    def test_sign_unknown_webhook_returns_empty(self, webhook_with_token):
        setup = webhook_with_token
        assert setup["webhooks"].sign_payload(999999, {"a": 1}) == {}

    def test_sign_without_private_key_returns_empty(self, webhook_with_token):
        setup = webhook_with_token
        setup["db"].execute(
            "UPDATE webhook_webhooks SET signing_key_private_encrypted = NULL WHERE id = ?",
            (setup["webhook"].id,),
        )
        assert setup["webhooks"].sign_payload(setup["webhook"].id, {"a": 1}) == {}

    def test_sign_with_corrupt_private_key_returns_empty(self, webhook_with_token):
        setup = webhook_with_token
        setup["db"].execute(
            "UPDATE webhook_webhooks SET signing_key_private_encrypted = ? WHERE id = ?",
            ("not-a-valid-encrypted-envelope", setup["webhook"].id),
        )
        assert setup["webhooks"].sign_payload(setup["webhook"].id, {"a": 1}) == {}


class TestVerifyPayload:
    def test_verify_round_trip_valid(self, signed):
        setup, payload, headers = signed
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
            )
            is True
        )

    def test_verify_rejects_tampered_payload(self, signed):
        setup, payload, headers = signed
        tampered = dict(payload)
        tampered["channel_id"] = 999
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                tampered,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
            )
            is False
        )

    def test_verify_rejects_wrong_signature(self, signed):
        setup, payload, headers = signed
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                "AAAA" + headers["X-Plexichat-Signature"][4:],
                headers["X-Plexichat-Timestamp"],
            )
            is False
        )

    def test_verify_rejects_replayed_timestamp(self, signed):
        setup, payload, headers = signed
        timestamp = int(headers["X-Plexichat-Timestamp"])
        # Replay far beyond the tolerance window.
        future_now = timestamp + WEBHOOK_REPLAY_WINDOW_SECONDS * 1000 + 1000
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
                now_ms=future_now,
            )
            is False
        )

    def test_verify_accepts_skew_within_window(self, signed):
        setup, payload, headers = signed
        timestamp = int(headers["X-Plexichat-Timestamp"])
        now_ms = timestamp + (WEBHOOK_REPLAY_WINDOW_SECONDS * 1000) // 2
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
                now_ms=now_ms,
            )
            is True
        )

    def test_verify_rejects_future_timestamp(self, signed):
        setup, payload, headers = signed
        timestamp = int(headers["X-Plexichat-Timestamp"])
        old_now = timestamp - WEBHOOK_REPLAY_WINDOW_SECONDS * 1000 - 1000
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
                now_ms=old_now,
            )
            is False
        )

    def test_verify_missing_headers_rejected(self, signed):
        setup, payload, _headers = signed
        assert (
            setup["webhooks"].verify_payload(setup["webhook"].id, payload, None, None)
            is False
        )
        assert (
            setup["webhooks"].verify_payload(setup["webhook"].id, payload, "", "")
            is False
        )

    def test_verify_non_numeric_timestamp_rejected(self, signed):
        setup, payload, headers = signed
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                "not-a-number",
            )
            is False
        )

    def test_verify_garbage_signature_rejected(self, signed):
        setup, payload, headers = signed
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                "!!!not-base64!!!",
                headers["X-Plexichat-Timestamp"],
            )
            is False
        )

    def test_verify_missing_public_key_rejected(self, webhook_with_token):
        setup = webhook_with_token
        payload = {"event": "x"}
        headers = setup["webhooks"].sign_payload(setup["webhook"].id, payload)
        setup["db"].execute(
            "UPDATE webhook_webhooks SET signing_key_public = NULL WHERE id = ?",
            (setup["webhook"].id,),
        )
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
            )
            is False
        )

    def test_verify_accepts_base64_text_public_key(self, webhook_with_token):
        import base64

        setup = webhook_with_token
        payload = {"event": "x"}
        headers = setup["webhooks"].sign_payload(setup["webhook"].id, payload)
        row = setup["db"].fetch_one(
            "SELECT signing_key_public FROM webhook_webhooks WHERE id = ?",
            (setup["webhook"].id,),
        )
        assert row is not None
        b64_text = base64.b64encode(bytes(row["signing_key_public"])).decode("ascii")
        setup["db"].execute(
            "UPDATE webhook_webhooks SET signing_key_public = ? WHERE id = ?",
            (b64_text, setup["webhook"].id),
        )
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
            )
            is True
        )

    def test_verify_rejects_wrong_length_raw_key(self, webhook_with_token):
        setup = webhook_with_token
        payload = {"event": "x"}
        headers = setup["webhooks"].sign_payload(setup["webhook"].id, payload)
        setup["db"].execute(
            "UPDATE webhook_webhooks SET signing_key_public = ? WHERE id = ?",
            (b"\x00\x01\x02", setup["webhook"].id),
        )
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
            )
            is False
        )

    def test_verify_unknown_webhook_id_rejected(self, signed):
        setup, payload, headers = signed
        assert (
            setup["webhooks"].verify_payload(
                999999,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
            )
            is False
        )

    def test_verify_rejects_replayed_signature(self, webhook_with_token):
        """F7: a captured valid (signature, timestamp) pair must be
        rejected on reuse within the replay window."""
        setup = webhook_with_token
        payload = {"event": "replay-me"}
        headers = setup["webhooks"].sign_payload(setup["webhook"].id, payload)

        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
            )
            is True
        )
        # Same pair a second time (classic capture-and-replay) -> rejected.
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers["X-Plexichat-Signature"],
                headers["X-Plexichat-Timestamp"],
            )
            is False
        )

    def test_reencoded_signature_still_verifies_when_not_replayed(
        self, webhook_with_token
    ):
        """A non-canonical encoding of a *fresh* signature is still valid.

        Whitespace is discarded by lenient base64 decoding, so a space-padded
        spelling of the header carries the identical signature bytes and must
        verify. This pins the baseline that the replay test below relies on:
        the re-encoded header is rejected there because it is a replay, not
        because the encoding is broken.
        """
        setup = webhook_with_token
        payload = {"event": "fresh-reencoded"}
        headers = setup["webhooks"].sign_payload(setup["webhook"].id, payload)
        signature = headers["X-Plexichat-Signature"]
        reencoded = signature[:8] + " " + signature[8:]
        assert reencoded != signature

        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                reencoded,
                headers["X-Plexichat-Timestamp"],
            )
            is True
        )

    def test_verify_rejects_reencoded_replay(self, webhook_with_token):
        """Regression: replay protection must key on the DECODED signature.

        Base64 admits several encodings of the same bytes. Before the fix the
        replay cache key hashed the raw header text, so re-encoding one valid
        signature (inserting whitespace, which lenient base64 discards)
        produced a *different* key and the replay was accepted inside the
        ±window. The key now derives from the decoded signature bytes, so
        every encoding of an already-consumed signature is rejected.
        """
        setup = webhook_with_token
        payload = {"event": "reencoded-replay"}
        headers = setup["webhooks"].sign_payload(setup["webhook"].id, payload)
        signature = headers["X-Plexichat-Signature"]
        timestamp = headers["X-Plexichat-Timestamp"]

        # First delivery consumes the (webhook_id, signature) pair.
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id, payload, signature, timestamp
            )
            is True
        )

        # Same signature BYTES re-encoded as a different header string -> must
        # still be recognised as the same, already-consumed signature.
        reencoded = signature[:8] + " " + signature[8:]
        assert reencoded != signature
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id, payload, reencoded, timestamp
            )
            is False
        )
