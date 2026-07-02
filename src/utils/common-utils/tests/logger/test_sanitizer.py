import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.logger.sanitizer import sanitize_data, sanitize_log_message


def test_sanitize_data_masks_expanded_sensitive_keys():
    payload = {
        "api_secret": "super-secret-value",
        "private_key": "private-key-material",
        "jwt": "header.payload.signature",
    }

    sanitized = sanitize_data(payload)

    assert sanitized["api_secret"] == "su***ue"
    assert sanitized["private_key"] == "pr***al"
    assert sanitized["jwt"] == "he***re"


def test_sanitize_log_message_masks_authorization_patterns():
    message = (
        "Authorization: Bearer abc.def.ghi api_key=xyz987 client_secret: topsecret"
    )

    sanitized = sanitize_log_message(message)

    assert "abc.def.ghi" not in sanitized
    assert "xyz987" not in sanitized
    assert "topsecret" not in sanitized
    assert (
        "Authorization: ********" in sanitized
        or "Authorization: Bearer ********" in sanitized
    )
