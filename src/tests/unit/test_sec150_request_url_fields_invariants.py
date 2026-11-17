"""SEC150 invariant: request-schema URL fields reject protocol-relative URLs
and embedded credentials.

Every URL carried in a request goes through the shared ``HttpUrl`` policy in
``src.api.schemas.url_types``. That policy delegates to
``require_http_url`` so a field can never be a relative reference (``//host``)
or smuggle credentials (``https://u:p@host``) -- both of which let a client read
a different authority than a reviewer expects.

This suite enumerates the concrete URL fields on request models and checks each
through a Pydantic ``TypeAdapter`` built from the field's own annotation, so a
field that quietly drops its annotation is caught. It also checks the shared
validator and the allowlist builder directly.
"""

from typing import Annotated, Union, get_args, get_origin

import pytest
from pydantic import TypeAdapter, ValidationError

from src.api.schemas.url_types import (
    allowlisted_http_url,
    validate_http_url,
)
from src.api.schemas.auth import OAuthCallbackRequest
from src.api.schemas.media import HashReportRequest
from src.api.schemas.messages import AttachmentRequest
from src.api.schemas.servers import ServerCreateRequest, ServerUpdateRequest
from src.api.schemas.users import UserUpdateRequest
from src.api.schemas.webhooks import (
    WebhookCreateRequest,
    WebhookExecuteRequest,
    WebhookUpdateRequest,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

# (label, model, field) for every request-model URL field. Kept explicit so the
# coverage guard cannot be weakened by a broad/gloomy discovery rule.
URL_FIELDS = (
    ("AttachmentRequest.url", AttachmentRequest, "url"),
    ("UserUpdateRequest.banner_url", UserUpdateRequest, "banner_url"),
    ("OAuthCallbackRequest.redirect_uri", OAuthCallbackRequest, "redirect_uri"),
    ("HashReportRequest.attachment_url", HashReportRequest, "attachment_url"),
    ("WebhookCreateRequest.avatar_url", WebhookCreateRequest, "avatar_url"),
    ("WebhookUpdateRequest.avatar_url", WebhookUpdateRequest, "avatar_url"),
    ("WebhookExecuteRequest.avatar_url", WebhookExecuteRequest, "avatar_url"),
    ("ServerCreateRequest.icon_url", ServerCreateRequest, "icon_url"),
    ("ServerUpdateRequest.icon_url", ServerUpdateRequest, "icon_url"),
)

PROTOCOL_RELATIVE = (
    "//evil.example/x",
    "///evil.example/x",
    "//user:pass@evil.example/x",
)

CREDENTIALED = (
    "https://user:pass@evil.example/x",
    "http://u:p@evil.example/",
    "https://user@evil.example/x",
)


def _field_policy(field):
    """The policy name declared by a field, including Optional[...] nesting."""

    def from_meta(metadata):
        return getattr(getattr(metadata, "func", None), "__policy_name__", None)

    for metadata in field.metadata:
        name = from_meta(metadata)
        if name:
            return name

    def scan(annotation):
        if get_origin(annotation) is Annotated:
            for metadata in get_args(annotation)[1:]:
                name = from_meta(metadata)
                if name:
                    return name
            return scan(get_args(annotation)[0])
        if get_origin(annotation) is Union:
            for argument in get_args(annotation):
                name = scan(argument)
                if name:
                    return name
        return None

    return scan(field.annotation)


def _adapter(model, field_name):
    field = model.model_fields[field_name]
    metadata = tuple(field.metadata)
    annotation = (
        Annotated[field.annotation, *metadata] if metadata else field.annotation
    )
    return TypeAdapter(annotation)


def test_every_url_field_declares_the_url_policy():
    for label, model, field_name in URL_FIELDS:
        field = model.model_fields[field_name]
        assert _field_policy(field) in {"http_url", "http_url_allowlisted"}, (
            f"{label} no longer carries the URL policy"
        )


@pytest.mark.parametrize(
    "label,model,field_name", URL_FIELDS, ids=[x[0] for x in URL_FIELDS]
)
@pytest.mark.parametrize("bad_url", PROTOCOL_RELATIVE + CREDENTIALED)
def test_request_url_fields_reject_protocol_relative_and_credentials(
    label, model, field_name, bad_url
):
    with pytest.raises(ValidationError):
        _adapter(model, field_name).validate_python(bad_url)


@pytest.mark.parametrize(
    "label,model,field_name", URL_FIELDS, ids=[x[0] for x in URL_FIELDS]
)
def test_request_url_fields_accept_a_plain_absolute_url(label, model, field_name):
    assert _adapter(model, field_name).validate_python("https://example.com/x") == (
        "https://example.com/x"
    )


@pytest.mark.parametrize("bad_url", PROTOCOL_RELATIVE + CREDENTIALED)
def test_shared_validator_and_allowlisted_builder_reject(bad_url):
    with pytest.raises(Exception):
        validate_http_url(bad_url, "url")
    Allowlisted = allowlisted_http_url("example.com")
    with pytest.raises(ValidationError):
        TypeAdapter(Allowlisted).validate_python(bad_url)


def test_shared_validator_accepts_absolute_url_and_allows_clearing():
    assert validate_http_url("https://example.com/x", "url") == "https://example.com/x"
    assert validate_http_url(None, "url") is None
    assert validate_http_url("", "url") == ""
