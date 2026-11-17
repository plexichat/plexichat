"""Regressions for webhook signature-verification hardening.

* non-canonical timestamp headers must be rejected before ``int()`` gets a
  chance to normalise them into the value that was actually signed;
* a signing-key rotation must not commit while a signature verification is
  in flight (the public-key fetch and the Ed25519 check share one lock with
  the rotation UPDATE, closing the old-key TOCTOU window).
"""

import threading

import pytest

from src.core.webhooks.manager.signature import (
    WEBHOOK_REPLAY_WINDOW_SECONDS,
    _is_canonical_timestamp_header,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

_SIG = "X-Plexichat-Signature"
_TS = "X-Plexichat-Timestamp"


class TestCanonicalTimestampHeader:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1700000000000", True),
            ("0", True),
            ("", False),
            ("+1700000000000", False),
            ("-1700000000000", False),
            ("01700000000000", False),
            ("1_700_000_000_000", False),
            (" 1700000000000", False),
            ("1700000000000 ", False),
            ("1700000000000\n", False),
            # Unicode decimal digits: int() accepts these, the signer never
            # emits them, so they must not be treated as an alias.
            ("١٧٠٠٠٠٠٠٠٠٠٠٠", False),
            ("not-a-number", False),
        ],
    )
    def test_helper_only_accepts_canonical_decimal(self, value, expected):
        assert _is_canonical_timestamp_header(value) is expected

    @pytest.mark.parametrize(
        "alias",
        [
            "+{ts}",
            "0{ts}",
            "{ts} ",
            " {ts}",
            "{ts}\n",
        ],
    )
    def test_noncanonical_timestamp_cannot_verify_a_valid_signature(
        self, webhook_with_token, alias
    ):
        """A signature is valid, but a non-canonical spelling of its
        timestamp must not be accepted: the signed bytes contain the
        canonical integer produced by the signer, so a parser that silently
        normalises `+ts` / `0ts` / whitespace accepts a header that does not
        match what was signed from the recipient's point of view.
        """
        setup = webhook_with_token
        manager = setup["webhooks"]
        wid = setup["webhook"].id
        # Distinct payloads/signatures for the positive control and the alias
        # attempt: reusing one signature would let the replay cache mask the
        # very bug this test is meant to expose.
        ok_payload = {"event": f"ts-canonical-{alias!r}"}
        ok_headers = manager.sign_payload(wid, ok_payload)
        canonical = ok_headers[_TS]

        assert (
            manager.verify_payload(
                wid, ok_payload, ok_headers[_SIG], canonical, now_ms=int(canonical)
            )
            is True
        )

        alias_payload = {"event": f"ts-alias-{alias!r}"}
        alias_headers = manager.sign_payload(wid, alias_payload)
        # Build the alias from the SAME timestamp the alias signature was made
        # with, so with the old lenient parser the signature genuinely verifies
        # and the test fails on the rejected header spelling (not a mismatch).
        alias_canonical = alias_headers[_TS]
        noncanonical = alias.format(ts=alias_canonical)
        assert noncanonical != alias_canonical
        assert (
            manager.verify_payload(
                wid,
                alias_payload,
                alias_headers[_SIG],
                noncanonical,
                now_ms=int(alias_canonical),
            )
            is False
        )

    def test_unicode_digit_timestamp_rejected(self, webhook_with_token):
        setup = webhook_with_token
        manager = setup["webhooks"]
        wid = setup["webhook"].id
        payload = {"event": "unicode-ts"}
        headers = manager.sign_payload(wid, payload)
        canonical = headers[_TS]
        arabic = canonical.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        assert int(arabic) == int(canonical)  # int() would accept the alias

        assert (
            manager.verify_payload(wid, payload, headers[_SIG], arabic, int(canonical))
            is False
        )

    def test_oversized_window_still_rejected(self, webhook_with_token):
        """The canonical spelling does not relax the replay window guard."""
        setup = webhook_with_token
        manager = setup["webhooks"]
        wid = setup["webhook"].id
        payload = {"event": "window-still-enforced"}
        headers = manager.sign_payload(wid, payload)
        ts = int(headers[_TS])
        assert (
            manager.verify_payload(
                wid,
                payload,
                headers[_SIG],
                headers[_TS],
                now_ms=ts + WEBHOOK_REPLAY_WINDOW_SECONDS * 1000 + 1000,
            )
            is False
        )


class TestRotationVerificationSerialization:
    def test_rotation_waits_for_inflight_verification(
        self, webhook_with_token, monkeypatch
    ):
        """The public-key fetch, Ed25519 check and rotation UPDATE must be
        mutually exclusive. Without the shared lock, a rotation can commit
        between the fetch and the verify, leaving a signature made with the
        retired key accepted against stale key material.
        """
        setup = webhook_with_token
        manager = setup["webhooks"]
        wid = setup["webhook"].id
        owner_id = setup["owner"].id
        payload = {"event": "rotation-toctou"}
        headers = manager.sign_payload(wid, payload)

        fetch_entered = threading.Event()
        release_fetch = threading.Event()
        verify_finished = threading.Event()
        rotation_update_started = threading.Event()
        rotation_finished = threading.Event()
        result = {}

        real_fetch_one = type(manager._db).fetch_one
        real_execute = type(manager._db).execute

        def gated_fetch(self, query, params=None, *args, **kwargs):
            row = real_fetch_one(self, query, params, *args, **kwargs)
            if "signing_key_public" in query and "SELECT" in query.upper():
                # Widen the fetch -> verify window and hold the verifier here.
                fetch_entered.set()
                assert release_fetch.wait(timeout=10), "verify fetch never released"
            return row

        def watching_execute(self, query, *args, **kwargs):
            if "update webhook_webhooks" in query.lower():
                rotation_update_started.set()
            return real_execute(self, query, *args, **kwargs)

        monkeypatch.setattr(type(manager._db), "fetch_one", gated_fetch)
        monkeypatch.setattr(type(manager._db), "execute", watching_execute)

        def verify_worker():
            try:
                result["verified"] = manager.verify_payload(
                    wid, payload, headers[_SIG], headers[_TS], int(headers[_TS])
                )
            except Exception as exc:  # pragma: no cover - surface thread errors
                result["verify_error"] = exc
            finally:
                verify_finished.set()

        def rotate_worker():
            try:
                manager.rotate_signing_key(wid, owner_id)
            except Exception as exc:  # pragma: no cover - surface thread errors
                result["rotate_error"] = exc
            finally:
                rotation_finished.set()

        verifier = threading.Thread(target=verify_worker)
        verifier.start()
        assert fetch_entered.wait(timeout=10), "verification never fetched the key"

        rotator = threading.Thread(target=rotate_worker)
        rotator.start()
        # With the shared lock the rotation cannot reach its UPDATE while the
        # verification holds the key; without it, this event fires quickly.
        assert not rotation_update_started.wait(timeout=1.0), (
            "signing-key rotation committed during an in-flight verification"
        )

        release_fetch.set()
        assert verify_finished.wait(timeout=10)
        assert rotator.join(timeout=10) is None
        assert rotation_finished.is_set()
        assert "verify_error" not in result, result
        assert "rotate_error" not in result, result
        assert result["verified"] is True

    def test_new_key_signatures_verify_after_serialized_rotation(
        self, webhook_with_token
    ):
        """Positive control: after a locked rotation the new key verifies and
        a never-replayed old-key signature is still rejected.
        """
        setup = webhook_with_token
        manager = setup["webhooks"]
        wid = setup["webhook"].id
        owner_id = setup["owner"].id

        old_payload = {"event": "pre-rotation"}
        old_headers = manager.sign_payload(wid, old_payload)

        manager.rotate_signing_key(wid, owner_id)

        assert (
            manager.verify_payload(
                wid,
                old_payload,
                old_headers[_SIG],
                old_headers[_TS],
                int(old_headers[_TS]),
            )
            is False
        )

        fresh = {"event": "post-rotation"}
        fresh_headers = manager.sign_payload(wid, fresh)
        assert (
            manager.verify_payload(
                wid,
                fresh,
                fresh_headers[_SIG],
                fresh_headers[_TS],
                int(fresh_headers[_TS]),
            )
            is True
        )
