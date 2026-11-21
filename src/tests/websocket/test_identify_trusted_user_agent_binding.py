"""IDENTIFY must bind the token to the trusted HTTP User-Agent header.

Token IP/UA binding stores a blind index of the *transport* User-Agent at
login time. IDENTIFY used to derive the verification UA from the client-
controlled ``properties.browser`` / ``$browser`` fields first, so a holder of
a stolen token could echo the victim's UA in IDENTIFY and defeat UA binding.
The header is the authoritative source (RESUME already uses it); client
properties remain telemetry only.
"""

import pytest

from src.api.websocket.opcodes import GatewayOpcode

HEADER_UA = "TestClient/1.0"
FORGED_UA = "Forged-Victim-UA"


def _verify_ua_call(auth):
    """Return the user_agent passed to the (single) verify_token call."""
    assert auth.verify_token.call_count == 1
    # verify_token(token, ip_address, user_agent, is_selftest=...)
    return auth.verify_token.call_args.args[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("prop_key", ["browser", "$browser"])
async def test_identify_uses_header_ua_not_client_properties(
    opcode_handler, connection, sample_identify_payload, prop_key
):
    """A forged properties UA must not reach the token binding check."""
    payload = dict(sample_identify_payload)
    payload["properties"] = {prop_key: FORGED_UA}

    _, _, close_code = await opcode_handler.handle(
        connection, GatewayOpcode.IDENTIFY, payload
    )

    assert close_code is None
    assert _verify_ua_call(opcode_handler._auth) == HEADER_UA
    # Telemetry is preserved: the client-supplied value still lands on the
    # connection properties even though it is not trusted for binding.
    assert connection.properties[prop_key] == FORGED_UA


@pytest.mark.asyncio
async def test_identify_missing_header_fails_closed_even_with_properties_ua(
    opcode_handler, connection, sample_identify_payload
):
    """No transport header means no binding context, never a client claim."""
    connection.websocket.headers = {}
    payload = dict(sample_identify_payload)
    payload["properties"] = {"browser": FORGED_UA}

    _, _, close_code = await opcode_handler.handle(
        connection, GatewayOpcode.IDENTIFY, payload
    )

    assert close_code is None
    assert _verify_ua_call(opcode_handler._auth) is None
