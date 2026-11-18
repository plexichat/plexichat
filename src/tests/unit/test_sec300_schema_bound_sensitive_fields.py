"""SEC-300 invariant: bound-sensitive request fields carry an enforced bound.

The HTTP body cap (10 MB) is a transport limit, not a field bound. A single
password, token or version string can stay under it while still being large
enough to make a synchronous KDF/PEP-440 parse expensive or to be persisted and
echoed to every client; a list can hold thousands of entries within the same
cap. This suite enumerates the fields whose values feed a hash/KDF, are
reflected to clients, or drive resource use, and pins the invariant that each
one is bounded *at the schema*, before any handler runs.

It is deliberately an explicit table rather than a name-pattern sweep: the
sweep in ``test_sec3_request_model_bounds`` only proves that fields which
*declare* a bound enforce it, and the policy meta-tests exempt free-text names.
Neither can tell you that a sensitive field is missing a bound entirely -- the
gap that let ``VersionNegotiateRequest`` accept an unbounded ``client_version``
and an unbounded ``supported_versions`` list.
"""

from typing import Annotated

import pytest
from pydantic import TypeAdapter, ValidationError

from src.api.schemas.admin.access_tokens import (
    AccessTokenCreateRequest,
    AccessTokenRotateRequest,
)
from src.api.schemas.admin.admin_auth import (
    AdminChangePasswordRequest,
    AdminLoginRequest,
    AdminOTPDisableRequest,
    AdminOTPSetupBeginRequest,
)
from src.api.schemas.admin.hash_reports import ManualBlockHashRequest
from src.api.schemas.admin.ip_management import IPBlockRequest
from src.api.schemas.admin.tickets import InternalNoteCreate
from src.api.schemas.auth import (
    LoginRequest,
    PasswordResetConfirm,
    RegisterRequest,
    TwoFactorDisableRequest,
    TwoFactorSetupRequest,
)
from src.api.schemas.dsar import DSARRequestBody
from src.api.schemas.feedback import FeedbackCreate
from src.api.schemas.telemetry import TelemetrySubmission
from src.api.schemas.users import UserDeletionRequest, UserUpdateRequest
from src.api.schemas.version import (
    MAX_CLIENT_VERSION_LENGTH,
    MAX_SUPPORTED_VERSION_LENGTH,
    MAX_SUPPORTED_VERSIONS,
    VersionNegotiateRequest,
)


def _max_length(field):
    for metadata in field.metadata:
        if type(metadata).__name__ == "MaxLen":
            return metadata.max_length
    return None


def _adapter(field) -> TypeAdapter:
    """A TypeAdapter that preserves the field's declared constraints."""
    if field.metadata:
        return TypeAdapter(Annotated[field.annotation, *field.metadata])
    return TypeAdapter(field.annotation)


#: (model, field) whose value reaches a password KDF, a token hash, a parser,
#: or is echoed back to clients. Every one must carry a length ceiling.
STRING_BOUND_FIELDS = [
    # Password/KDF inputs.
    (RegisterRequest, "password"),
    (LoginRequest, "password"),
    (TwoFactorSetupRequest, "password"),
    (TwoFactorDisableRequest, "password"),
    (PasswordResetConfirm, "new_password"),
    (AdminLoginRequest, "password"),
    (AdminChangePasswordRequest, "current_password"),
    (AdminChangePasswordRequest, "new_password"),
    (AdminOTPSetupBeginRequest, "current_password"),
    (AdminOTPDisableRequest, "current_password"),
    (UserUpdateRequest, "password"),
    (UserUpdateRequest, "current_password"),
    (UserDeletionRequest, "password"),
    (DSARRequestBody, "password"),
    # User-supplied tokens / secrets, hashed before storage.
    (AccessTokenCreateRequest, "token"),
    (AccessTokenRotateRequest, "token"),
    # Values parsed by a format helper and/or reflected in a response.
    (ManualBlockHashRequest, "hash_value"),
    (ManualBlockHashRequest, "reason"),
    (FeedbackCreate, "content"),
    (FeedbackCreate, "category"),
    (InternalNoteCreate, "content"),
    # The fields fixed by SEC-300: previously unbounded at the schema.
    (VersionNegotiateRequest, "client_version"),
]

#: (model, field, one_valid_item). The list itself must have a length ceiling,
#: and each enumerated item must be a valid element of that list.
LIST_BOUND_FIELDS = [
    (VersionNegotiateRequest, "supported_versions", "1.0.0"),
    (
        TelemetrySubmission,
        "entries",
        {
            "endpoint": "/x",
            "method": "GET",
            "response_time_ms": 1.0,
            "status_code": 200,
        },
    ),
]

#: (model, field, valid_value, below_min, above_max) for numeric range fields.
NUMERIC_BOUND_FIELDS = [
    (FeedbackCreate, "rating", 3, 0, 6),
    (IPBlockRequest, "duration_hours", 24, 0, 8761),
]


@pytest.mark.parametrize("model,field_name", STRING_BOUND_FIELDS)
def test_sensitive_string_is_length_bounded(model, field_name):
    field = model.model_fields[field_name]
    limit = _max_length(field)
    assert limit is not None, f"{model.__name__}.{field_name} has no max_length"
    assert limit > 0
    adapter = _adapter(field)
    # The ceiling is accepted; one character over it is rejected at the
    # schema boundary (before any KDF/parser/handler work).
    adapter.validate_python("a" * (limit - 1))
    with pytest.raises(ValidationError):
        adapter.validate_python("a" * (limit + 1))


@pytest.mark.parametrize("model,field_name,item", LIST_BOUND_FIELDS)
def test_sensitive_list_is_length_bounded(model, field_name, item):
    field = model.model_fields[field_name]
    limit = _max_length(field)
    assert limit is not None, f"{model.__name__}.{field_name} has no max_length"
    adapter = _adapter(field)
    adapter.validate_python([item] * limit)
    with pytest.raises(ValidationError):
        adapter.validate_python([item] * (limit + 1))


@pytest.mark.parametrize("model,field_name,item", LIST_BOUND_FIELDS)
def test_sensitive_list_items_are_bounded(model, field_name, item):
    """A capped list of uncapped items is still an unbounded input sink."""
    field = model.model_fields[field_name]
    adapter = _adapter(field)
    if isinstance(item, str):
        oversized_item = "a" * (MAX_SUPPORTED_VERSION_LENGTH + 1)
        with pytest.raises(ValidationError):
            adapter.validate_python([oversized_item])
    else:
        # Structured items are bounded by their own model; an over-large free
        # text field in one must not slip through.
        with pytest.raises(ValidationError):
            adapter.validate_python([{**item, "endpoint": "a" * 256}])


@pytest.mark.parametrize("model,field_name,valid,below,above", NUMERIC_BOUND_FIELDS)
def test_sensitive_number_is_range_bounded(model, field_name, valid, below, above):
    adapter = _adapter(model.model_fields[field_name])
    adapter.validate_python(valid)
    with pytest.raises(ValidationError):
        adapter.validate_python(below)
    with pytest.raises(ValidationError):
        adapter.validate_python(above)


def test_version_bound_constants_and_route_parity():
    assert MAX_CLIENT_VERSION_LENGTH == 128
    assert MAX_SUPPORTED_VERSIONS == 16
    assert MAX_SUPPORTED_VERSION_LENGTH == 128
    # The route's legacy guard must stay in lockstep with the schema ceiling;
    # drift in either direction would reopen a parser-differential gap.
    from src.api.routes.version import MAX_CLIENT_VERSION_LENGTH as ROUTE_MAX

    assert MAX_CLIENT_VERSION_LENGTH == ROUTE_MAX


def test_enumerated_fields_really_exist():
    # Guard against a stale table silently shrinking the sweep.
    assert len(STRING_BOUND_FIELDS) >= 20
    assert len(LIST_BOUND_FIELDS) >= 2
    assert len(NUMERIC_BOUND_FIELDS) >= 2
    for model, field_name in STRING_BOUND_FIELDS:
        assert field_name in model.model_fields
    for model, field_name, _ in LIST_BOUND_FIELDS:
        assert field_name in model.model_fields
    for model, field_name, *_ in NUMERIC_BOUND_FIELDS:
        assert field_name in model.model_fields


def test_detector_is_not_vacuous():
    from pydantic import BaseModel, Field

    class Synthetic(BaseModel):
        unbounded: str = Field(...)
        bounded: str = Field(..., max_length=10)

    assert _max_length(Synthetic.model_fields["unbounded"]) is None
    assert _max_length(Synthetic.model_fields["bounded"]) == 10
