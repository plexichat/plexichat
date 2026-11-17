"""Hostile-input fuzzing of the request boundary.

A request model is a 4xx contract: anything a client can put in a JSON body
must either be validated into a safe value or refused with a 4xx. This module
fuzzes the three inputs that repeatedly turned into real bugs in this tree --
control characters (header/log injection, bidi spoofing), oversized/negative
integers (SQL offsets, buffer sizes, snowflake lookups) and hostile URLs
(SSRF, javascript:/data: sinks) -- against the actual Pydantic models and the
shared route-level id guards.

Model-level rejections are the 422/400 equivalent: the API's validation
exception handler maps ``ValidationError`` to a 4xx. Two HTTP-level checks at
the bottom confirm that mapping end to end.
"""

import types
import typing

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from src.api.routes.artifacts.validation import parse_snowflake_id as artifact_parse_id
from src.api.routes.features.common import parse_id as feature_parse_id
from src.api.routes.features.reports import EnhancedReportRequest
from src.api.routes.messages.helpers import require_snowflake
from src.api.schemas.channels import ChannelCreateRequest
from src.api.schemas.common import SnowflakeID
from src.api.schemas.media import HashReportRequest
from src.api.schemas.servers import ServerCreateRequest, ServerUpdateRequest
from src.api.schemas.stash import StashItemCreateRequest
from src.api.schemas.users import UserUpdateRequest
from src.api.schemas.webhooks import WebhookCreateRequest, WebhookUpdateRequest
from src.api.websocket.boundaries import parse_snowflake
from src.core.security.hardening import HardeningViolation, bounded_int, parse_id

CONTROL_CHAR_VALUES = (
    "bad\x00name",  # NUL truncation
    "bad\r\nX-Injected: 1",  # header/log splitting
    "bad\x7fname",  # DEL
)

# Bidirectional/invisible controls are rejected by the shared text policy;
# fields that only filter C0/DEL (channel names) are tested separately above.
BIDI_VALUES = (
    "bad\u202ename",  # bidi override (spoofed display name)
    "bad\u200fname",  # invisible directional mark
)

BAD_SCHEME_URL_VALUES = (
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "not a url at all",
)

# Links that are stored for a *server* to fetch must be resolved through the
# SSRF gate. Browser-only links (webhook avatars) are scheme-checked but not
# resolved; those are covered by BAD_SCHEME_URL_VALUES only.
SSRF_URL_VALUES = (
    "http://127.0.0.1:8080/admin",  # loopback
    "http://[::1]/admin",  # IPv6 loopback
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
)

HOSTILE_INT_VALUES = (
    -1,
    0,
    2**63,  # just past the signed 64-bit snowflake ceiling
    2**70,
    "not-an-int",
    "1; DROP TABLE users",
    "12.5",
)

# The features report model validates *positivity* but not the 64-bit ceiling;
# oversized ids are covered by the bounded guards below.
HOSTILE_INT_NON_DECIMAL = (-1, 0, "not-an-int", "1; DROP TABLE users", "12.5")

# Values with extra format constraints that a generic generator cannot infer.
_BENIGN_BY_FIELD = {
    "hash_value": "a" * 32,  # HashReportRequest: min_length=16
    "item_type": "file",  # StashItemCreateRequest: enum pattern
    "target_type": "user",  # EnhancedReportRequest: 'message'|'user'
    "target_id": "1",  # EnhancedReportRequest: bounded id validator
    "channel_id": "1",  # SnowflakeID, not a plain str
}


def _benign(name: str, annotation):
    """A harmless value for a required field we are not fuzzing."""
    if name in _BENIGN_BY_FIELD:
        return _BENIGN_BY_FIELD[name]
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _benign(name, args[0]) if args else None
    if origin in (list, typing.List):
        return []
    if origin in (dict, typing.Dict):
        return {}
    if isinstance(annotation, type):
        if issubclass(annotation, SnowflakeID):
            return "1"
        if issubclass(annotation, str):
            return "ok"
        if issubclass(annotation, bool):
            return False
        if issubclass(annotation, int):
            return 1
    return None


def _payload(model: type[BaseModel], field: str, value) -> dict:
    data = {
        name: _benign(name, info.annotation)
        for name, info in model.model_fields.items()
        if info.is_required()
    }
    data[field] = value
    return data


def _assert_model_rejects(model: type[BaseModel], field: str, value):
    # First prove the un-fuzzed payload is valid, so a rejection is genuinely
    # caused by the hostile value and not by a badly constructed base payload.
    baseline = _payload(
        model, field, _benign(field, model.model_fields[field].annotation)
    )
    model(**baseline)
    with pytest.raises(ValidationError):
        model(**_payload(model, field, value))


class TestControlCharacterFuzz:
    @pytest.mark.parametrize(
        "model,field",
        [
            (ChannelCreateRequest, "name"),
            (WebhookCreateRequest, "name"),
            (WebhookUpdateRequest, "name"),
            (UserUpdateRequest, "username"),
            (StashItemCreateRequest, "tags"),
        ],
    )
    @pytest.mark.parametrize("value", CONTROL_CHAR_VALUES)
    def test_control_characters_are_refused(self, model, field, value):
        if field == "tags":
            value = [value]
        _assert_model_rejects(model, field, value)

    @pytest.mark.parametrize(
        "model,field",
        [
            (WebhookCreateRequest, "name"),
            (WebhookUpdateRequest, "name"),
            (UserUpdateRequest, "username"),
            (StashItemCreateRequest, "tags"),
        ],
    )
    @pytest.mark.parametrize("value", BIDI_VALUES)
    def test_bidirectional_controls_are_refused_by_text_policy(
        self, model, field, value
    ):
        if field == "tags":
            value = [value]
        _assert_model_rejects(model, field, value)


class TestHostileUrlFuzz:
    @pytest.mark.parametrize(
        "model,field",
        [
            (HashReportRequest, "attachment_url"),
            (ServerCreateRequest, "icon_url"),
            (ServerUpdateRequest, "icon_url"),
            (WebhookCreateRequest, "avatar_url"),
            (WebhookUpdateRequest, "avatar_url"),
        ],
    )
    @pytest.mark.parametrize("value", BAD_SCHEME_URL_VALUES)
    def test_bad_url_schemes_are_refused(self, model, field, value):
        _assert_model_rejects(model, field, value)

    @pytest.mark.parametrize(
        "model,field",
        [
            (HashReportRequest, "attachment_url"),
            (ServerCreateRequest, "icon_url"),
            (ServerUpdateRequest, "icon_url"),
        ],
    )
    @pytest.mark.parametrize("value", SSRF_URL_VALUES)
    def test_internal_urls_are_refused_by_the_ssrf_gate(self, model, field, value):
        _assert_model_rejects(model, field, value)


class TestHostileIntegerFuzz:
    @pytest.mark.parametrize("value", HOSTILE_INT_NON_DECIMAL)
    def test_report_target_id_rejects_hostile_integers(self, value):
        # A nested request model whose field_validator delegates to the shared
        # bounded id guard; Pydantic turns the refusal into a 422.
        _assert_model_rejects(EnhancedReportRequest, "target_id", str(value))

    @pytest.mark.parametrize("value", HOSTILE_INT_VALUES)
    @pytest.mark.parametrize(
        "guard",
        [parse_id, require_snowflake, artifact_parse_id],
    )
    def test_route_id_guards_raise_a_4xx_or_hardening_error(self, guard, value):
        with pytest.raises((HTTPException, HardeningViolation, ValueError)):
            guard(str(value), "id")

    @pytest.mark.parametrize(
        "value", [-1, 0, "not-an-int", "1; DROP TABLE users", "12.5"]
    )
    def test_feature_parse_id_rejects_non_positive_or_non_decimal(self, value):
        with pytest.raises(HTTPException):
            feature_parse_id(str(value), "id")

    @pytest.mark.parametrize("value", HOSTILE_INT_VALUES)
    def test_gateway_snowflake_parser_rejects_hostile_values(self, value):
        with pytest.raises(HardeningViolation):
            parse_snowflake(value, "id")

    @pytest.mark.parametrize("value", HOSTILE_INT_VALUES)
    def test_bounded_int_rejects_values_past_the_ceiling(self, value):
        with pytest.raises(HardeningViolation):
            bounded_int(value, "limit", minimum=1, maximum=2**63 - 1)


def _validate_snowflake(value):
    from pydantic import TypeAdapter

    return TypeAdapter(SnowflakeID).validate_python(value)


class TestSnowflakeModelFuzz:
    @pytest.mark.parametrize(
        "value", [-1, "abc", "1; DROP TABLE users", "12.5", True, None, [], {}]
    )
    def test_snowflake_id_rejects_non_ids(self, value):
        with pytest.raises(ValidationError):
            _validate_snowflake(value)


class TestHttpLevelFourXx:
    def test_control_characters_in_channel_name_return_4xx(
        self, test_client, auth_headers
    ):
        response = test_client.post(
            "/api/v1/servers/1/channels",
            headers=auth_headers,
            json={"name": "bad\x00name", "type": 0},
        )
        assert 400 <= response.status_code < 500, response.text

    def test_control_characters_in_registration_return_4xx(self, test_client):
        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": "bad\x00name",
                "email": "fuzz@example.com",
                "password": "TestPass123!",
            },
        )
        assert 400 <= response.status_code < 500, response.text
