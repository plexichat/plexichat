"""Interaction callback payloads must be bounded before persistence.

``POST /applications/interactions/{token}/callback`` accepts a free-form
``data`` object whose values are forwarded into an ``InteractionResponse``,
serialized, encrypted and stored. Without ceilings a holder of a (short-lived,
single-use) interaction token could amplify that one response into megabytes of
stored/encrypted JSON and render it to every client. These tests pin the new
bounds while confirming normal payloads still pass.
"""

import pytest
from unittest.mock import patch
from fastapi import HTTPException

from src.api.routes import applications as routes
from src.core.applications.models import InteractionResponseType


def _body(data):
    return routes.InteractionCallbackRequest(
        application_id=1,
        type=InteractionResponseType.PONG,
        data=data,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        # A single unbounded text field.
        {"content": "A" * (routes.MAX_CALLBACK_CONTENT_LENGTH + 1)},
        # Too many collection entries.
        {"embeds": [{}] * (routes.MAX_CALLBACK_EMBEDS + 1)},
        {"attachments": [{}] * (routes.MAX_CALLBACK_ATTACHMENTS + 1)},
        {"choices": [{}] * (routes.MAX_CALLBACK_CHOICES + 1)},
        # One oversized complex object.
        {
            "allowed_mentions": {
                "parse": "x" * (routes.MAX_CALLBACK_COMPLEX_JSON_LENGTH + 1)
            }
        },
        # Per-field limits are fine individually but the total is too large.
        {
            "embeds": [
                {"description": "x" * 7000} for _ in range(routes.MAX_CALLBACK_EMBEDS)
            ]
        },
    ],
)
async def test_oversized_callback_payload_is_rejected(data):
    body = _body(data)
    with patch.object(routes, "check_rate_limit", return_value=(True, 29)):
        with patch.object(
            routes.applications, "create_interaction_response"
        ) as create_response:
            with pytest.raises(HTTPException) as exc:
                await routes.respond_to_interaction("int.1.secret", body)

    assert exc.value.status_code == 400
    create_response.assert_not_called()


@pytest.mark.asyncio
async def test_bounded_callback_payload_is_forwarded():
    body = _body(
        {
            "content": "hello",
            "embeds": [{"title": "ok"}],
            "title": "done",
        }
    )
    with patch.object(routes, "check_rate_limit", return_value=(True, 29)):
        with patch.object(
            routes.applications, "create_interaction_response", return_value=True
        ) as create_response:
            result = await routes.respond_to_interaction("int.1.secret", body)

    assert result is None
    create_response.assert_called_once()
    response = create_response.call_args.kwargs["response"]
    assert response.content == "hello"
    assert response.embeds == [{"title": "ok"}]
    assert response.title == "done"
