"""SEC200b invariant: request URL fields reject relative refs and credentials.

Any URL accepted by a request model is either echoed into a browser or fetched
on a user's behalf. Two shapes must never pass validation:

* protocol-relative references (``//evil.example/x``) -- the reviewer sees no
  scheme, but a client resolves it against the ambient scheme;
* embedded credentials (``https://u:p@evil.example/x``) -- the authority a
  human reads differs from the one the HTTP client authenticates to.

Every request URL field carries the shared ``HttpUrl`` policy from
``src.api.schemas.url_types``. This suite discovers those fields straight from
the schema source (AST), so a field that silently drops its ``HttpUrl``
annotation is caught by a coverage mismatch. Each field is then exercised
through a ``TypeAdapter`` built from its own annotation with a broader
adversarial payload set than the basic validator checks, and the shared
validator is checked directly.

A detector self-test proves the payload set is non-vacuous and that a normal
absolute URL is still accepted.
"""

import ast
from pathlib import Path
from typing import Annotated, Union, get_args, get_origin

import pytest
from pydantic import TypeAdapter, ValidationError

from src.api.schemas.url_types import allowlisted_http_url, validate_http_url
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

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = ROOT / "src" / "api" / "schemas"

# (model, field) for every request-model field annotated with HttpUrl.
URL_FIELDS = (
    (AttachmentRequest, "url"),
    (UserUpdateRequest, "banner_url"),
    (OAuthCallbackRequest, "redirect_uri"),
    (HashReportRequest, "attachment_url"),
    (WebhookCreateRequest, "avatar_url"),
    (WebhookUpdateRequest, "avatar_url"),
    (WebhookExecuteRequest, "avatar_url"),
    (ServerCreateRequest, "icon_url"),
    (ServerUpdateRequest, "icon_url"),
)

PROTOCOL_RELATIVE = (
    "//evil.example/x",
    "///evil.example/x",
    "/\\evil.example",
    "//user:pass@evil.example/x",
)

CREDENTIALED = (
    "https://user:pass@evil.example/x",
    "http://u:p@evil.example/",
    "https://user@evil.example/x",
    "https://:p@evil.example/x",
    "https://u:@evil.example/x",
)

CONFUSION = (
    "https:///evil.example",  # empty netloc, path hosts the authority
    "https://example.com\\@evil.example",  # backslash/parser differential
    "https://example.com\nX-Evil: 1",  # CR/LF header injection
    "javascript:alert(1)",
    "data:text/html,x",
)

BAD_URLS = PROTOCOL_RELATIVE + CREDENTIALED + CONFUSION


def _discover_url_fields():
    """AST-discover every ``HttpUrl``-annotated field in the schema package."""
    found = set()
    for path in sorted(SCHEMAS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for stmt in cls.body:
                if not (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                ):
                    continue
                if any(
                    isinstance(n, ast.Name) and n.id == "HttpUrl"
                    for n in ast.walk(stmt.annotation)
                ):
                    found.add((cls.name, stmt.target.id))
    return found


def _policy_name(annotation):
    """Find the attached policy name, including through Optional/Annotated."""

    def from_meta(meta):
        return getattr(getattr(meta, "func", None), "__policy_name__", None)

    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        for meta in args[1:]:
            name = from_meta(meta)
            if name:
                return name
        return _policy_name(args[0])
    if origin is Union:
        for arg in get_args(annotation):
            name = _policy_name(arg)
            if name:
                return name
    return None


def _field_policy(field):
    """Policy name declared by a field, via metadata or nested annotation."""
    for meta in field.metadata:
        name = getattr(getattr(meta, "func", None), "__policy_name__", None)
        if name:
            return name
    return _policy_name(field.annotation)


def _adapter(model, field_name):
    field = model.model_fields[field_name]
    metadata = tuple(field.metadata)
    annotation = (
        Annotated[field.annotation, *metadata] if metadata else field.annotation
    )
    return TypeAdapter(annotation)


def test_discovered_fields_match_the_explicit_manifest():
    """No Url field may be added/dropped without updating this contract."""
    discovered = _discover_url_fields()
    expected = {(model.__name__, field) for model, field in URL_FIELDS}
    assert discovered == expected, (
        f"undeclared: {discovered - expected}; stale: {expected - discovered}"
    )


def test_every_url_field_carries_the_http_url_policy():
    for model, field_name in URL_FIELDS:
        field = model.model_fields[field_name]
        name = _field_policy(field)
        assert name in {"http_url", "http_url_allowlisted"}, (
            f"{model.__name__}.{field_name} no longer carries the URL policy"
        )


@pytest.mark.parametrize(
    "model,field_name", URL_FIELDS, ids=[f"{m.__name__}.{f}" for m, f in URL_FIELDS]
)
@pytest.mark.parametrize("bad_url", BAD_URLS)
def test_request_url_fields_reject_relative_and_credentialed(model, field_name, bad_url):
    with pytest.raises(ValidationError):
        _adapter(model, field_name).validate_python(bad_url)


@pytest.mark.parametrize(
    "model,field_name", URL_FIELDS, ids=[f"{m.__name__}.{f}" for m, f in URL_FIELDS]
)
def test_request_url_fields_accept_a_plain_absolute_url(model, field_name):
    assert _adapter(model, field_name).validate_python("https://example.com/x") == (
        "https://example.com/x"
    )


@pytest.mark.parametrize("bad_url", BAD_URLS)
def test_shared_validator_and_allowlisted_builder_reject(bad_url):
    with pytest.raises(Exception):
        validate_http_url(bad_url, "url")
    Allowlisted = allowlisted_http_url("example.com")
    with pytest.raises(ValidationError):
        TypeAdapter(Allowlisted).validate_python(bad_url)


def test_payload_set_is_non_vacuous():
    """Self-test: the bad set must mix relative + credentialed + confusion."""
    assert any("//" in u and "://" not in u for u in PROTOCOL_RELATIVE)
    assert any("@" in u for u in CREDENTIALED)
    assert any("\\" in u or "\n" in u for u in CONFUSION)
    # Shared validator still accepts a normal URL and optional clears.
    assert validate_http_url("https://example.com/x", "url") == "https://example.com/x"
    assert validate_http_url(None, "url") is None
    assert validate_http_url("", "url") == ""
