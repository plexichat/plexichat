"""Non-vacuous audit regression for password hashing.

Exercises the *real* peppered Argon2id implementation (no mocks of the KDF or
the verifier) to prove four properties:

1. a modern KDF (Argon2id) with bounded/adequate cost is used;
2. verification is correct and equalizes timing on the empty/short-circuit
   paths instead of returning early without touching the hasher;
3. legacy / malformed / bad hashes fail closed (never authenticate);
4. a plaintext or empty password is never what gets stored.

All hashes are produced by ``EncryptionManager.hash_password`` with a real
software pepper, so a regression in any layer (envelope parsing, pepper HMAC,
Argon2 params, empty-password guard) makes these tests fail.
"""

import base64
import hashlib
import secrets

import pytest


@pytest.fixture
def managers(monkeypatch):
    """Build real managers backed by an ephemeral env-based software pepper.

    Returns ``(production_cost_manager, fast_manager)``. Both use the real
    Argon2id hasher and the real pepper HMAC; the second only lowers the cost so
    the fail-closed battery stays quick. Low cost does not change verification
    semantics, so it is still a non-vacuous exercise of the code under audit.
    """
    from src.utils.encryption import pepper as pepper_mod

    key_hex = secrets.token_hex(32)
    monkeypatch.setenv("AUDIT_PEPPER_V1", key_hex)
    monkeypatch.setenv("AUDIT_PEPPER_V2", secrets.token_hex(32))

    import utils.config as config_mod

    real_get = config_mod.get

    def fake_get(key, default=None):
        if key == "encryption":
            return {
                "password_pepper": {
                    "enabled": True,
                    "current_id": "v1",
                    "peppers": {"v1": {"source": "env", "env_var": "AUDIT_PEPPER_V1"}},
                },
                "hsm": {},
            }
        return real_get(key, default)

    monkeypatch.setattr(config_mod, "get", fake_get)
    pepper_mod.reset_pepper_store()

    from src.utils.encryption.core import EncryptionManager

    prod = EncryptionManager()  # defaults: t=2, m=65536, p=2
    fast = EncryptionManager(
        argon2_time_cost=1, argon2_memory_cost=8192, argon2_parallelism=1
    )
    try:
        yield prod, fast
    finally:
        pepper_mod.reset_pepper_store()


def _phc_params(envelope):
    """Return (variant, {name: int}, salt_bytes, digest_bytes) from a hash."""
    from src.utils.encryption.pepper import parse_envelope

    parsed = parse_envelope(envelope)
    assert parsed is not None, f"expected pepper envelope, got {envelope!r}"
    pepper_id, inner = parsed
    parts = inner.split("$")
    # $argon2id$v=19$m=..,t=..,p=..$<salt>$<digest>
    assert parts[1].startswith("argon2"), inner
    params = dict(chunk.split("=", 1) for chunk in parts[3].split(","))
    params = {k: int(v) for k, v in params.items()}
    salt = base64.b64decode(parts[4] + "=" * (-len(parts[4]) % 4))
    digest = base64.b64decode(parts[5] + "=" * (-len(parts[5]) % 4))
    return pepper_id, parts[1], params, salt, digest


@pytest.mark.security
class TestPasswordHashingAudit:
    def test_modern_kdf_with_bounded_cost(self, managers):
        prod, _ = managers
        pw = "CorrectHorse9!"
        envelope = prod.hash_password(pw)

        pid, variant, params, salt, digest = _phc_params(envelope)
        assert pid == "v1", "hash must record the pepper id"
        assert variant == "argon2id", "only Argon2id is acceptable"
        # Adequate, bounded cost: at least OWASP's 19 MiB / t=2 / p=1 floor,
        # with an upper sanity bound so a misconfig cannot turn one login into
        # a CPU/memory denial of service.
        assert 19456 <= params["m"] <= 1_048_576, params
        assert 2 <= params["t"] <= 10, params
        assert 1 <= params["p"] <= 16, params
        assert len(salt) >= 16, len(salt)
        assert len(digest) >= 16, len(digest)

    def test_verify_correct_and_wrong(self, managers):
        _, fast = managers
        envelope = fast.hash_password("RightPassword123!")
        assert fast.verify_password("RightPassword123!", envelope) is True
        assert fast.verify_password("rightpassword123!", envelope) is False
        assert fast.verify_password("RightPassword1234", envelope) is False

    def test_verify_equalizes_empty_path_timing(self, managers, monkeypatch):
        """Empty/missing inputs still run a real hasher verification.

        A correct verification returns True; a wrong one runs Argon2 to
        completion and then fails. On the empty-password short circuit the
        code must *also* invoke the hasher (timing ballast), not return early
        in a way that leaks "empty password" via response time.
        """
        _, fast = managers

        class RecordingHasher:
            """Delegate to the real C hasher while counting verify calls."""

            def __init__(self, real):
                self._real = real
                self.calls = []

            def hash(self, password):
                return self._real.hash(password)

            def verify(self, hash_str, password):
                self.calls.append(password)
                return self._real.verify(hash_str, password)

            def check_needs_rehash(self, hash_str):
                return self._real.check_needs_rehash(hash_str)

        recorder = RecordingHasher(fast.password_hasher)
        fast.password_hasher = recorder
        envelope = fast.hash_password("TimingProbe123!")

        recorder.calls.clear()
        assert fast.verify_password("", envelope) is False
        assert recorder.calls, "empty-password path must still exercise the hasher"

        recorder.calls.clear()
        assert fast.verify_password("TimingProbe123!", envelope) is True
        assert recorder.calls

        recorder.calls.clear()
        assert fast.verify_password("nope", envelope) is False
        assert recorder.calls

    @pytest.mark.parametrize(
        "bad_hash",
        [
            "plaintext-password",  # stored plaintext
            "5d41402abc4b2a76b9719d911017c592",  # legacy MD5
            hashlib.sha256(b"CorrectHorse9!").hexdigest(),  # legacy SHA-256
            "$argon2id$v=19$m=8,t=1,p=1$c2FsdA$YWJj",  # bare/unpeppered Argon2
            "$2b$12$abcdefghijklmnopqrstuuABCDEFGHIJKLMNOPQRSTUVWXYZ012",  # bcrypt
            "$plexichat$pepper-v1$pid=v1$not-an-argon2-hash",  # broken envelope
            "$plexichat$pepper-v1$pid=unknown$anything",  # unknown pepper id
            "$plexichat$pepper-v1$pid=v1$5d41402abc4b2a76b9719d911017c592",  # wrapped digest
            "",  # empty
        ],
    )
    def test_legacy_and_bad_hashes_fail_closed(self, managers, bad_hash):
        _, fast = managers
        # Even with the exact plaintext the bad hash claims to represent, it
        # must never authenticate.
        assert fast.verify_password("CorrectHorse9!", bad_hash) is False

    def test_plaintext_and_empty_never_stored(self, managers):
        prod, _ = managers
        pw = "NeverStoreMe123!"
        with pytest.raises(ValueError):
            prod.hash_password("")
        with pytest.raises(ValueError):
            prod.hash_password(None)

        envelope = prod.hash_password(pw)
        assert envelope != pw
        assert pw not in envelope
        # The store holds a pepper envelope + Argon2 PHC, never a raw digest.
        _, variant, _, _, _ = _phc_params(envelope)
        assert variant == "argon2id"

    def test_unpeppered_hash_cannot_be_promoted(self, managers):
        _, fast = managers
        raw = fast.password_hasher.hash("Unpeppered123!")
        assert not raw.startswith("$plexichat$")
        assert fast.verify_password("Unpeppered123!", raw) is False
        assert fast.needs_rehash(raw) is False
