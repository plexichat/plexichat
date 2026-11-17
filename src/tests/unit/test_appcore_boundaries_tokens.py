"""Length and format boundaries for client secrets and OAuth tokens."""

import pytest

from src.core.applications.oauth.tokens import (
    MAX_TOKEN_LENGTH,
    generate_client_secret,
    generate_access_token,
    parse_oauth_token,
    validate_client_secret,
    validate_token_hash,
    validate_token_string,
    hash_token,
    verify_token_hash,
)
from src.core.applications.oauth.flows import OAuth2Flow
from src.core.applications.exceptions import InvalidClientError, InvalidGrantError
from src.core.security.hardening import HardeningViolation


class TestSecretAndTokenValidation:
    def test_generated_values_are_accepted(self):
        secret, _ = generate_client_secret()
        assert validate_client_secret(secret) == secret
        token, _ = generate_access_token(123)
        assert validate_token_string(token) == token
        parsed = parse_oauth_token(token)
        assert parsed is not None
        assert parsed == {"token_type": "access", "id": 123, "secret": parsed["secret"]}

    @pytest.mark.parametrize(
        "secret", ["", "a" * 300, "has spaces here", "has\ncontrol", "bad!token"]
    )
    def test_rejects_bad_client_secrets(self, secret):
        with pytest.raises(HardeningViolation):
            validate_client_secret(secret)

    def test_token_string_length_cap(self):
        assert validate_token_string("a" * MAX_TOKEN_LENGTH)
        with pytest.raises(HardeningViolation):
            validate_token_string("a" * (MAX_TOKEN_LENGTH + 1))
        with pytest.raises(HardeningViolation):
            validate_token_string("line\nbreak")

    def test_hash_format(self):
        digest = hash_token("secret")
        assert validate_token_hash(digest) == digest
        for bad in ("", "zzz", digest[:-1], digest + "ff", None):
            with pytest.raises(HardeningViolation):
                validate_token_hash(bad)

    def test_verify_rejects_malformed_inputs_without_raising(self):
        digest = hash_token("secret-value-123")
        assert verify_token_hash("secret-value-123", digest) is True
        assert verify_token_hash("wrong-secret-value", digest) is False
        assert verify_token_hash("a" * (MAX_TOKEN_LENGTH + 1), digest) is False
        assert verify_token_hash("secret-value-123", "not-a-hash") is False

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "not-a-token",
            "access.0." + "a" * 20,  # zero is not a snowflake
            "access.-1." + "a" * 20,
            "access.1." + "a" * (MAX_TOKEN_LENGTH + 1),
            "access.1.bad secret",
            "unknown.1." + "a" * 20,
        ],
    )
    def test_parse_rejects_malformed_tokens(self, token):
        assert parse_oauth_token(token) is None


class TestFlowBoundary:
    def _flow(self, app_manager):
        return OAuth2Flow(
            app_manager._db,
            {"code_expiry_seconds": 600, "token_expiry_seconds": 604800},
        )

    def test_exchange_rejects_bad_secret_and_token(self, app_manager, test_user):
        app = app_manager.create_application(owner_id=test_user.id, name="Token App")
        flow = self._flow(app_manager)
        with pytest.raises(InvalidClientError):
            flow.exchange_code(
                app.id, "short", "access.1." + "a" * 20, "https://x.io/cb"
            )
        with pytest.raises(InvalidGrantError):
            flow.exchange_code(
                app.id,
                "a" * 32,
                "a" * (MAX_TOKEN_LENGTH + 1),
                "https://x.io/cb",
            )

    def test_verify_rejects_oversized_token(self, app_manager):
        flow = self._flow(app_manager)
        with pytest.raises(InvalidGrantError):
            flow.verify_access_token("a" * (MAX_TOKEN_LENGTH + 1))

    def test_generator_length_is_bounded(self):
        from src.core.security.hardening import HardeningViolation as HV

        with pytest.raises(HV):
            generate_client_secret(length=1)
        with pytest.raises(HV):
            generate_client_secret(length=10_000)
