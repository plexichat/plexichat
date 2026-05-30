import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.logger.sanitizer import sanitize_data, sanitize_log_message


def test_sanitize_data_masks_expanded_sensitive_keys():
    payload = {
        "api_secret": "super-secret-value",  # pragma: allowlist secret
        "private_key": "private-key-material",  # pragma: allowlist secret
        "jwt": "header.payload.signature",  # pragma: allowlist secret
    }

    sanitized = sanitize_data(payload)

    assert sanitized["api_secret"] == "su***ue"  # pragma: allowlist secret
    assert sanitized["private_key"] == "pr***al"  # pragma: allowlist secret
    assert sanitized["jwt"] == "he***re"  # pragma: allowlist secret


def test_sanitize_log_message_masks_authorization_patterns():
    message = "Authorization: Bearer abc.def.ghi api_key=xyz987 client_secret: topsecret"  # pragma: allowlist secret

    sanitized = sanitize_log_message(message)

    assert "abc.def.ghi" not in sanitized
    assert "xyz987" not in sanitized
    assert "topsecret" not in sanitized
    assert (
        "Authorization: ********" in sanitized
        or "Authorization: Bearer ********" in sanitized
    )
