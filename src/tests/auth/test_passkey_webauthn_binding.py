"""Regression guard: WebAuthn origin / RP ID / user binding is decided server-side.

The passkey verification path must *never* derive its expected origin or RP ID
from client-controlled data.  A WebAuthn assertion carries two independent
origin-ish values:

* ``clientDataJSON.origin`` (cryptographically bound via the signature base), and
* ``clientExtensionResults`` / a top-level ``origin`` field (pure client JSON,
  not signed by the authenticator).

The ``webauthn`` library checks the signed ``clientDataJSON.origin`` against the
``expected_origin`` passed by the server, and the attestation/assertion
``rpIdHash`` against ``expected_rp_id``.  This test drives the real library with
hand-crafted ES256 assertions to prove that a credential registered for one
RP/origin cannot authenticate when the origin or RP ID is changed, and that
attacker-supplied ``origin`` / ``clientExtensionResults.appid`` values cannot
override the server configuration.

If a future change passed ``credential_response["origin"]``,
``credential_response["clientExtensionResults"]["appid"]``, or any other
client-supplied value as ``expected_origin``/``expected_rp_id``, the
"wrong origin" / "client origin is ignored" cases below would be accepted and
this test would fail.
"""

import base64
import hashlib
import json
import os
import struct

import pytest

webauthn = pytest.importorskip("webauthn")
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import (  # noqa: E402
    decode_dss_signature,
    encode_dss_signature,
)

from src.core.auth.passkeys import PasskeyManager  # noqa: E402


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _client_data(challenge: bytes, origin: str, typ: str = "webauthn.get") -> bytes:
    return json.dumps(
        {
            "type": typ,
            "challenge": _b64u(challenge),
            "origin": origin,
            "crossOrigin": False,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _auth_data(rp_id: str, sign_count: int, up: bool = True, uv: bool = True) -> bytes:
    flags = (0x01 if up else 0) | (0x04 if uv else 0)
    return hashlib.sha256(rp_id.encode("utf-8")).digest() + bytes([flags]) + struct.pack(
        ">I", sign_count
    )


def _cose_es256(public_key) -> bytes:
    """Minimal COSE_Key (ES256) for the webauthn library to decode."""
    import cbor2

    nums = public_key.public_numbers()
    return cbor2.dumps(
        {
            1: 2,  # kty: EC2
            3: -7,  # alg: ES256
            -1: 1,  # crv: P-256
            -2: nums.x.to_bytes(32, "big"),
            -3: nums.y.to_bytes(32, "big"),
        }
    )


def _assertion(
    challenge: bytes,
    credential_id_b64: str,
    private_key,
    *,
    origin: str,
    rp_id: str,
    sign_count: int = 1,
    client_origin_field: str | None = None,
    client_rp_id_field: str | None = None,
    extension_appid: str | None = None,
) -> dict:
    client_data = _client_data(challenge, origin)
    auth_data = _auth_data(rp_id, sign_count)
    signature_base = auth_data + hashlib.sha256(client_data).digest()
    der_sig = private_key.sign(signature_base, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)

    credential = {
        "id": credential_id_b64,
        "rawId": credential_id_b64,
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64u(client_data),
            "authenticatorData": _b64u(auth_data),
            "signature": _b64u(encode_dss_signature(r, s)),
        },
    }
    # Attacker-controlled, unsigned fields.  A correct RP ignores these when
    # deciding which origin/RP ID the assertion belongs to.
    if client_origin_field is not None:
        credential["origin"] = client_origin_field
    if client_rp_id_field is not None:
        credential["rpId"] = client_rp_id_field
    credential["clientExtensionResults"] = {}
    if extension_appid is not None:
        credential["clientExtensionResults"]["appid"] = extension_appid
    return credential


@pytest.fixture
def binding_manager(db, monkeypatch):
    """A PasskeyManager pinned to a known RP ID / origin, with two users."""
    mgr = PasskeyManager(db)
    # Pin config deterministically instead of relying on host-derived defaults.
    monkeypatch.setattr(mgr, "_rp_id", "chat.example.com")
    monkeypatch.setattr(mgr, "_config", {"origin": "https://chat.example.com"})
    mgr._ensure_tables()
    for user_id, name, handle in ((1, "alice", "YWxpY2U="), (2, "bob", "Ym9i")):
        db.execute(
            "INSERT INTO auth_users "
            "(id, username, password_hash, permissions, created_at, updated_at, "
            " webauthn_user_handle) VALUES (?, ?, ?, ?, 0, 0, ?)",
            (user_id, name, "x", "[]", handle),
        )
    return mgr


def _register_credential(db, mgr, user_id, rp_id):
    """Insert a credential as if previously registered for ``rp_id``."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    credential_id = os.urandom(32)
    credential_id_b64 = _b64u(credential_id)
    db.execute(
        "INSERT INTO auth_passkeys "
        "(id, user_id, credential_id, credential_public_key, sign_count, created_at) "
        "VALUES (?, ?, ?, ?, 0, 0)",
        (user_id * 1000, user_id, credential_id_b64, _cose_es256(private_key.public_key())),
    )
    return credential_id_b64, private_key


def _fresh_challenge(db, mgr):
    options = mgr.generate_authentication_options(username="alice")
    row = db.fetch_one(
        "SELECT challenge FROM auth_passkey_challenges WHERE challenge_id = ?",
        (options.challenge_id,),
    )
    return options.challenge_id, row["challenge"]


class TestWebAuthnOriginAndRPBinding:
    def test_legitimate_assertion_authenticates(self, db, binding_manager):
        """The happy path still works: correct origin + RP ID is accepted."""
        cred_id, key = _register_credential(db, binding_manager, 1, "chat.example.com")
        challenge_id, challenge = _fresh_challenge(db, binding_manager)
        credential = _assertion(
            challenge,
            cred_id,
            key,
            origin="https://chat.example.com",
            rp_id="chat.example.com",
        )
        result = binding_manager.verify_authentication(challenge_id, credential)
        assert result["user_id"] == 1
        assert result["credential_id"] == cred_id

    def test_wrong_origin_is_rejected(self, db, binding_manager):
        """An assertion signed for a different origin must fail closed."""
        cred_id, key = _register_credential(db, binding_manager, 1, "chat.example.com")
        challenge_id, challenge = _fresh_challenge(db, binding_manager)
        credential = _assertion(
            challenge,
            cred_id,
            key,
            origin="https://evil.example",
            rp_id="chat.example.com",
        )
        with pytest.raises(ValueError, match="origin|verification failed"):
            binding_manager.verify_authentication(challenge_id, credential)

    def test_wrong_rp_id_is_rejected(self, db, binding_manager):
        """An assertion whose rpIdHash is for another RP must fail closed."""
        cred_id, key = _register_credential(db, binding_manager, 1, "chat.example.com")
        challenge_id, challenge = _fresh_challenge(db, binding_manager)
        credential = _assertion(
            challenge,
            cred_id,
            key,
            origin="https://chat.example.com",
            rp_id="evil.example",  # rpIdHash does not match server config
        )
        with pytest.raises(ValueError, match="RP ID|rp id|verification failed"):
            binding_manager.verify_authentication(challenge_id, credential)

    def test_client_supplied_origin_and_extension_cannot_override(self, db, binding_manager):
        """Unsigned client fields must not redirect the expected origin/RP ID.

        The attacker sends an assertion for the real origin/RP (so it is
        legitimately signed) but ALSO stuffs a top-level ``origin`` and a
        ``clientExtensionResults.appid`` pointing at their own domain.  A
        server that trusted those fields would accept an assertion that is
        actually bound to a different origin; the correct server checks only
        the signed clientDataJSON + the configured RP ID and accepts (or, if
        the signed origin is wrong, rejects) regardless of the attacker's
        extra fields.
        """
        cred_id, key = _register_credential(db, binding_manager, 1, "chat.example.com")

        # Signed origin is WRONG -> attacker tries to "fix" it with client data.
        challenge_id, challenge = _fresh_challenge(db, binding_manager)
        credential = _assertion(
            challenge,
            cred_id,
            key,
            origin="https://evil.example",
            rp_id="chat.example.com",
            client_origin_field="https://chat.example.com",
            extension_appid="https://chat.example.com",
        )
        with pytest.raises(ValueError):
            binding_manager.verify_authentication(challenge_id, credential)

        # Signed origin is RIGHT, attacker injects a DIFFERENT appid -> must
        # still succeed: the unsigned extension is ignored, not used as RP ID.
        challenge_id, challenge = _fresh_challenge(db, binding_manager)
        credential = _assertion(
            challenge,
            cred_id,
            key,
            origin="https://chat.example.com",
            rp_id="chat.example.com",
            client_origin_field="https://evil.example",
            extension_appid="https://evil.example",
        )
        result = binding_manager.verify_authentication(challenge_id, credential)
        assert result["user_id"] == 1

    def test_client_supplied_rp_id_cannot_override(self, db, binding_manager):
        """A client ``rpId`` field must not redirect the expected RP ID.

        The assertion's rpIdHash is signed for ``evil.example``; the attacker
        stuffs a top-level ``rpId=chat.example.com`` hoping the server trusts
        it.  The real server compares the signed hash with its configured RP
        ID and rejects.  A server that used the client ``rpId`` would wrongly
        accept.
        """
        cred_id, key = _register_credential(db, binding_manager, 1, "chat.example.com")
        challenge_id, challenge = _fresh_challenge(db, binding_manager)
        credential = _assertion(
            challenge,
            cred_id,
            key,
            origin="https://chat.example.com",
            rp_id="evil.example",  # signed rpIdHash is for evil.example
            client_rp_id_field="chat.example.com",  # attacker's override attempt
        )
        with pytest.raises(ValueError):
            binding_manager.verify_authentication(challenge_id, credential)

        # Conversely: a correctly-signed assertion for the configured RP must
        # still succeed even when a client ``rpId`` tries to redirect it.
        # A server that trusted the client field would reject it (RP ID hash
        # mismatch), so this half of the test guards the fail-safe direction.
        challenge_id, challenge = _fresh_challenge(db, binding_manager)
        credential = _assertion(
            challenge,
            cred_id,
            key,
            origin="https://chat.example.com",
            rp_id="chat.example.com",  # signed rpIdHash is correct
            client_rp_id_field="evil.example",  # ignored by a correct server
        )
        result = binding_manager.verify_authentication(challenge_id, credential)
        assert result["user_id"] == 1

    def test_credential_does_not_authenticate_another_user(self, db, binding_manager):
        """A passkey registered for Alice resolves to Alice, never Bob.

        Even when Bob supplies the challenge and injects his own user handle,
        verification keys off the stored credential -> user mapping.
        """
        alice_cred, alice_key = _register_credential(
            db, binding_manager, 1, "chat.example.com"
        )
        _bob_cred, bob_key = _register_credential(
            db, binding_manager, 2, "chat.example.com"
        )

        # Bob asks for options but presents Alice's credential.
        options = binding_manager.generate_authentication_options(username="bob")
        row = db.fetch_one(
            "SELECT challenge FROM auth_passkey_challenges WHERE challenge_id = ?",
            (options.challenge_id,),
        )
        credential = _assertion(
            row["challenge"],
            alice_cred,
            alice_key,
            origin="https://chat.example.com",
            rp_id="chat.example.com",
        )
        result = binding_manager.verify_authentication(options.challenge_id, credential)
        assert result["user_id"] == 1  # Alice, not Bob

        # A credential signed by Bob's key but claiming Alice's id fails the
        # signature check (public key is looked up by the presented id).
        options = binding_manager.generate_authentication_options(username="alice")
        row = db.fetch_one(
            "SELECT challenge FROM auth_passkey_challenges WHERE challenge_id = ?",
            (options.challenge_id,),
        )
        forged = _assertion(
            row["challenge"],
            alice_cred,
            bob_key,  # wrong private key for Alice's credential id
            origin="https://chat.example.com",
            rp_id="chat.example.com",
        )
        with pytest.raises(ValueError):
            binding_manager.verify_authentication(options.challenge_id, forged)
