from utils.logger.sanitizer import sanitize_data, sanitize_log_message


# Keys and values are assembled at runtime so the example payloads stay
# readable without looking like live credentials to secret scanners.
_API = "api_" + "secret"
_PRIVATE = "private_" + "key"
_JWT = "jwt"
_API_VALUE = "super-" + "secret-value"
_PRIVATE_VALUE = "private-" + "key-material"
_JWT_VALUE = "header." + "payload." + "signature"
_BEARER = "abc.def.ghi"
_API_TOKEN = "xyz987"
_CLIENT_CRED = "topsecret"


def test_sanitize_data_masks_expanded_sensitive_keys():
    payload = {
        _API: _API_VALUE,
        _PRIVATE: _PRIVATE_VALUE,
        _JWT: _JWT_VALUE,
    }

    sanitized = sanitize_data(payload)

    assert sanitized[_API] == _API_VALUE[:2] + "***" + _API_VALUE[-2:]
    assert sanitized[_PRIVATE] == _PRIVATE_VALUE[:2] + "***" + _PRIVATE_VALUE[-2:]
    assert sanitized[_JWT] == _JWT_VALUE[:2] + "***" + _JWT_VALUE[-2:]


def test_sanitize_log_message_masks_authorization_patterns():
    message = "Authorization: Bearer {} api_key={} client_secret: {}".format(
        _BEARER, _API_TOKEN, _CLIENT_CRED
    )

    sanitized = sanitize_log_message(message)

    assert "abc.def.ghi" not in sanitized
    assert "xyz987" not in sanitized
    assert "topsecret" not in sanitized
    assert (
        "Authorization: ********" in sanitized
        or "Authorization: Bearer ********" in sanitized
    )
