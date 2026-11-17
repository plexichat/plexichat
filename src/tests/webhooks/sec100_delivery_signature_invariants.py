"""Security invariants: webhook deliveries require a valid signature.

Deterministic checks of the Ed25519 delivery signature format and of the
``verify_payload`` gate. The cryptographic cases use a fixed private-key seed
and a fixed timestamp, so no case depends on the clock; the route-level cases
pass an explicit ``now_ms`` equal to the signed timestamp, so skew is zero.

* only full 64-byte Ed25519 signatures pass the structural guard;
* the version, timestamp and canonical JSON body are all bound into the
  signature (tampering with any of them invalidates it);
* replay-cache keys derive from the decoded signature bytes, so alternative
  base64 encodings of one signature cannot bypass replay protection;
* ``verify_payload`` refuses missing, malformed, tampered and wrong-length
  headers even when the rest of the delivery is well-formed.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.webhooks.manager.constants import (
    SIGNATURE_HEADER,
    SIGNATURE_TIMESTAMP_HEADER,
    SIGNATURE_VERSION,
)
from src.core.webhooks.manager.signature import (
    ED25519_SIGNATURE_LENGTH,
    _replay_cache_key,
    _signature_length_is_valid,
)
from src.utils.encryption import sign_data, verify_signature

pytestmark = [pytest.mark.unit, pytest.mark.security]

# A fixed 32-byte seed makes the derived keypair and every signature below
# reproducible across runs and platforms.
_SEED = bytes(range(32))
_TIMESTAMP = "1700000000000"
_WEBHOOK_ID = 42
_PAYLOAD = {"event": "message.created", "channel_id": 42, "nested": {"a": [1, 2]}}


def _keypair():
    private = Ed25519PrivateKey.from_private_bytes(_SEED)
    private_bytes = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_bytes, public_bytes


def _signed_bytes(payload=None, *, version=SIGNATURE_VERSION, timestamp=_TIMESTAMP):
    body = json.dumps(
        _PAYLOAD if payload is None else payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{version}.{timestamp}.".encode("utf-8") + body


def _signature(payload=None, *, version=SIGNATURE_VERSION, timestamp=_TIMESTAMP):
    private_bytes, _ = _keypair()
    return sign_data(
        _signed_bytes(payload, version=version, timestamp=timestamp), private_bytes
    )


class TestSignatureFormatInvariants:
    @pytest.mark.parametrize(
        "length,expected",
        [(0, False), (1, False), (32, False), (63, False), (64, True), (65, False), (128, False)],
    )
    def test_only_full_ed25519_signature_passes_structural_guard(self, length, expected):
        assert ED25519_SIGNATURE_LENGTH == 64
        assert _signature_length_is_valid(b"\x00" * length) is expected

    @pytest.mark.parametrize(
        "encoder,decoder",
        [
            (lambda raw: base64.b64encode(raw).decode("ascii"), base64.b64decode),
            (
                lambda raw: base64.urlsafe_b64encode(raw).decode("ascii"),
                base64.urlsafe_b64decode,
            ),
            # Lenient decoders discard whitespace; the replay key must key on
            # bytes, not on the header spelling.
            (
                lambda raw: base64.b64encode(raw).decode("ascii")[:8]
                + " "
                + base64.b64encode(raw).decode("ascii")[8:],
                lambda text: base64.b64decode(text.replace(" ", "")),
            ),
        ],
    )
    def test_replay_key_is_stable_across_base64_encodings(self, encoder, decoder):
        signature = _signature()
        header = encoder(signature)
        assert decoder(header) == signature
        assert _replay_cache_key(_WEBHOOK_ID, signature) == _replay_cache_key(
            _WEBHOOK_ID, decoder(header)
        )

    def test_replay_key_differs_for_different_signatures(self):
        other = _signature(payload={"event": "message.deleted"})
        assert _replay_cache_key(_WEBHOOK_ID, _signature()) != _replay_cache_key(
            _WEBHOOK_ID, other
        )


class TestEd25519CryptoInvariants:
    def test_valid_signature_verifies(self):
        _private, public = _keypair()
        assert verify_signature(
            _signed_bytes(), _signature(), public
        ) is True

    @pytest.mark.parametrize(
        "payload",
        [
            {"event": "message.deleted", "channel_id": 42, "nested": {"a": [1, 2]}},
            {"event": "message.created", "channel_id": 43, "nested": {"a": [1, 2]}},
            {"event": "message.created", "channel_id": 42, "nested": {"a": [1]}},
        ],
    )
    def test_tampered_payload_invalidates_signature(self, payload):
        _private, public = _keypair()
        assert verify_signature(
            _signed_bytes(payload), _signature(), public
        ) is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"version": "v2"},
            {"timestamp": "1700000000001"},
        ],
    )
    def test_tampered_version_or_timestamp_invalidates_signature(self, kwargs):
        _private, public = _keypair()
        assert verify_signature(
            _signed_bytes(**kwargs), _signature(), public
        ) is False

    def test_reordered_json_keys_are_canonicalized(self):
        _private, public = _keypair()
        reordered = {"nested": {"a": [1, 2]}, "channel_id": 42, "event": "message.created"}
        assert verify_signature(
            _signed_bytes(reordered), _signature(), public
        ) is True

    def test_wrong_public_key_invalidates_signature(self):
        other_private = Ed25519PrivateKey.from_private_bytes(bytes(reversed(_SEED)))
        other_public = other_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        assert verify_signature(_signed_bytes(), _signature(), other_public) is False

    @pytest.mark.parametrize("mutate", [lambda s: s[:-1], lambda s: s[:1], lambda s: bytes([s[0] ^ 1]) + s[1:]])
    def test_corrupted_signature_invalidates(self, mutate):
        _private, public = _keypair()
        assert verify_signature(
            _signed_bytes(), mutate(_signature()), public
        ) is False


@pytest.fixture
def signed_delivery(webhook_with_token):
    setup = webhook_with_token
    payload = {"event": "message.created", "channel_id": 42}
    headers = setup["webhooks"].sign_payload(setup["webhook"].id, payload)
    assert SIGNATURE_HEADER in headers and SIGNATURE_TIMESTAMP_HEADER in headers
    return setup, payload, headers


class TestVerifyPayloadGate:
    def test_valid_delivery_signature_accepted(self, signed_delivery):
        setup, payload, headers = signed_delivery
        timestamp = int(headers[SIGNATURE_TIMESTAMP_HEADER])
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                headers[SIGNATURE_HEADER],
                headers[SIGNATURE_TIMESTAMP_HEADER],
                now_ms=timestamp,
            )
            is True
        )

    @pytest.mark.parametrize(
        "signature, timestamp, payload",
        [
            (None, _TIMESTAMP, _PAYLOAD),
            ("", _TIMESTAMP, _PAYLOAD),
            ("not-base64!!!", _TIMESTAMP, _PAYLOAD),
            ("AAAA", _TIMESTAMP, _PAYLOAD),
            ("A" * 1024, _TIMESTAMP, _PAYLOAD),
        ],
    )
    def test_malformed_or_missing_signature_rejected(
        self, signed_delivery, signature, timestamp, payload
    ):
        setup, _signed_payload, _headers = signed_delivery
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                payload,
                signature,
                timestamp,
                now_ms=int(timestamp),
            )
            is False
        )

    def test_tampered_payload_rejected(self, signed_delivery):
        setup, payload, headers = signed_delivery
        timestamp = int(headers[SIGNATURE_TIMESTAMP_HEADER])
        tampered = dict(payload, channel_id=999)
        assert (
            setup["webhooks"].verify_payload(
                setup["webhook"].id,
                tampered,
                headers[SIGNATURE_HEADER],
                headers[SIGNATURE_TIMESTAMP_HEADER],
                now_ms=timestamp,
            )
            is False
        )

    def test_wrong_webhook_id_rejected(self, signed_delivery):
        setup, payload, headers = signed_delivery
        timestamp = int(headers[SIGNATURE_TIMESTAMP_HEADER])
        assert (
            setup["webhooks"].verify_payload(
                999999999,
                payload,
                headers[SIGNATURE_HEADER],
                headers[SIGNATURE_TIMESTAMP_HEADER],
                now_ms=timestamp,
            )
            is False
        )
