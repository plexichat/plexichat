"""
Common schemas - Shared Pydantic models.
"""

import json
from functools import partial
from typing import Optional, List, Any, Generic, TypeVar, Annotated

from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, BeforeValidator, Field, ConfigDict, GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from src.core.security.policies import HTML_META_CHARS, RejectUICharacters
from src.core.security.hardening.numbers import SNOWFLAKE_MAX, bounded_int

from .policy_types import reject_unsafe_unicode


# A snowflake is a non-negative 64-bit identifier. Python ints are unbounded,
# so an unvalidated ``"999999999999999999999999"`` used to sail through and
# reach lookups, cache keys and offset arithmetic. The lower bound stays at 0
# (callers use 0 as a "no id yet"/"clear" sentinel); the upper bound is the
# hardening constant shared with path/query id parsing.
def _check_snowflake_upper_bound(numeric: int) -> None:
    if numeric > SNOWFLAKE_MAX:
        raise ValueError(f"Invalid snowflake ID: must be at most {SNOWFLAKE_MAX}")


def _parse_snowflake_string(value: str, field: str = "snowflake ID") -> int:
    """Parse a snowflake's string form with the shared ASCII-only id gate.

    These schema types and the ``snowflake_*`` helpers used to call
    ``int(text)`` directly. ``int`` accepts Unicode decimal homoglyphs
    (``"١٢٣"``, ``"１２３"``) and PEP 515 underscores (``"1_2"``), so one raw
    token was accepted here but rejected by every route-boundary id guard
    (``parse_id`` / ``bounded_int``), which are ASCII-only. A request field
    typed ``SnowflakeID`` and the same id parsed off a path/query therefore
    disagreed about whether the bytes were a valid snowflake. Route the string
    branch of every snowflake conversion through the one canonical gate so
    validation and consumption share a single interpretation of the token.

    Leading/trailing whitespace, a leading ``+`` and leading zeros remain
    accepted, matching ``bounded_int`` (and the values the API has always
    normalised). Only non-ASCII digits, separators and out-of-range values are
    newly rejected. ``HardeningViolation`` is a ``ValueError``, so callers and
    Pydantic keep their existing rejection contract.
    """
    return bounded_int(value, field, minimum=0, maximum=SNOWFLAKE_MAX)


# One JSON encoder configuration for every hand-built API response.
#
# ``starlette.responses.JSONResponse.render`` re-states its ``json.dumps``
# options (``ensure_ascii``/``allow_nan``/``separators``) on every single
# render. The API builds a number of small JSON responses itself (413 bodies,
# storage-quota errors, CORS denials) — funnelling them all through this one
# preconfigured callable keeps the option set in a single place and removes
# that per-call keyword setup.
#
# NOTE: we deliberately do NOT set this as FastAPI's ``default_response_class``.
# Recent FastAPI uses a Rust-core ``TypeAdapter.dump_json`` fast path for
# response-model endpoints, but only while the default response class is still
# the placeholder; installing a concrete class here would silently disable
# that fast path for every route. Model-backed responses keep using Pydantic's
# serialization (see ``validated_json_response`` below).
api_json_dumps = partial(
    json.dumps,
    ensure_ascii=False,
    allow_nan=False,
    indent=None,
    separators=(",", ":"),
)


class APIJSONResponse(JSONResponse):
    """A ``JSONResponse`` that reuses the shared API encoder configuration."""

    def render(self, content: Any) -> bytes:
        return api_json_dumps(content).encode("utf-8")


def validated_json_response(model: BaseModel, status_code: int = 200) -> Response:
    """Serialize an already-validated response model straight to JSON bytes.

    FastAPI's normal response path calls ``field.validate()`` on whatever a
    handler returns, even when that value is already an instance of the
    declared ``response_model``. A handler that constructs the model itself
    (the root/health endpoints do) has already paid that validation cost at
    construction time; this helper skips the duplicate ``validate_python``
    pass and serializes through Pydantic's Rust core via ``model_dump_json`` —
    byte-for-byte the same output FastAPI's own fast path produces.

    Validation is not weakened: the caller must pass a real ``BaseModel``
    instance (validated on construction), and the route keeps its explicit
    ``response_model`` for OpenAPI/docs.
    """
    return Response(
        content=model.model_dump_json(by_alias=True),
        status_code=status_code,
        media_type="application/json",
    )


T = TypeVar("T")


class SnowflakeID(str):
    """Snowflake ID represented as string for JSON serialization.

    Accepts both int and str, always serializes as string.
    """

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Define how Pydantic should validate this type."""
        # A plain validator receives the raw value (so ``bool``/negative
        # inputs can be rejected), but on its own emits no JSON schema. The
        # ``json_schema_input_schema`` keeps the declared OpenAPI type honest:
        # ``anyOf: [integer, string]``, so a generated client knows the id may
        # arrive as a string.
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            json_schema_input_schema=core_schema.union_schema(
                [core_schema.int_schema(), core_schema.str_schema()]
            ),
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def _validate(cls, v: Any) -> str:
        """Validate and convert to string."""
        if isinstance(v, bool):
            raise ValueError("Invalid snowflake ID")
        if isinstance(v, int):
            if v < 0:
                raise ValueError("Invalid snowflake ID: must be non-negative")
            _check_snowflake_upper_bound(v)
            return cls(str(v))
        if isinstance(v, str):
            numeric = _parse_snowflake_string(v)
            return cls(str(numeric))
        raise ValueError(f"Invalid snowflake ID type: {type(v)}")


class SnowflakeIDParam(int):
    """A snowflake id for a path/query param that yields a plain ``int``.

    URL path segments and query values are always strings, and Python ints
    are arbitrary precision, so a handler that receives the value as an
    ``int`` loses nothing. Accepting a numeric string as well as an int keeps
    the generated OpenAPI schema honest (``integer|string``) so a client is
    never told to coerce a 64-bit id through a JS ``number``.
    """

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        # See ``SnowflakeID``: a plain validator for the raw-value checks plus
        # an explicit JSON-schema input so the param declares ``integer|string``
        # rather than coming out untyped.
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            json_schema_input_schema=core_schema.union_schema(
                [core_schema.int_schema(), core_schema.str_schema()]
            ),
        )

    @classmethod
    def _validate(cls, v: Any) -> int:
        if isinstance(v, bool):
            raise ValueError("Invalid snowflake ID")
        if isinstance(v, int):
            if v < 0:
                raise ValueError("Invalid snowflake ID: must be non-negative")
            _check_snowflake_upper_bound(v)
            return cls(v)
        if isinstance(v, str):
            numeric = _parse_snowflake_string(v)
            return cls(numeric)
        raise ValueError(f"Invalid snowflake ID type: {type(v)}")


def _coerce_nullable_int(value: Any) -> Any:
    """Coerce ``null``/``none``/empty query values for optional int params."""
    if (
        value is None
        or value == ""
        or (isinstance(value, str) and value.strip().lower() == "null")
    ):
        return None
    return value


# Optional snowflake id query param: accepts a decimal string, yields an int
# (or ``None`` for a null/empty value).
NullableSnowflakeIDParam = Annotated[
    Optional[SnowflakeIDParam], BeforeValidator(_coerce_nullable_int)
]


class ErrorDetail(BaseModel):
    """Error detail model."""

    model_config = ConfigDict(from_attributes=True)

    code: int = Field(..., description="Error code")
    message: str = Field(..., description="Error message")


class ErrorResponse(BaseModel):
    """Standard error response."""

    model_config = ConfigDict(from_attributes=True)

    error: ErrorDetail


class PaginationParams(BaseModel):
    """Pagination parameters."""

    model_config = ConfigDict(from_attributes=True)

    limit: int = Field(
        default=50, ge=1, le=100, description="Number of items to return"
    )
    before: Optional[SnowflakeID] = Field(
        default=None, description="Get items before this ID"
    )
    after: Optional[SnowflakeID] = Field(
        default=None, description="Get items after this ID"
    )


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""

    model_config = ConfigDict(from_attributes=True)

    items: List[T]
    has_more: bool = False
    total: Optional[int] = None


class SuccessResponse(BaseModel):
    """Simple success response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(True, description="Whether the operation was successful")
    message: Optional[str] = Field(None, description="Optional success message")


class RootResponse(BaseModel):
    """Root endpoint response."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., description="API name")
    version: str = Field(..., description="API version")
    docs: str = Field(..., description="API docs URL")
    api: str = Field(..., description="API prefix")
    api_docs: Optional[str] = Field(
        default=None, description="Detailed API documentation path"
    )


def snowflake_to_str(v: Any) -> Optional[str]:
    """Convert snowflake ID to its canonical decimal string.

    Mirrors :class:`SnowflakeID`: rejects ``bool``, negative ints and
    non-numeric strings, and normalizes (``" 00123 "`` -> ``"123"``) so a
    caller mixing this helper with ``SnowflakeID`` cannot smuggle an
    invalid or non-canonical id through.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("Invalid snowflake ID")
    if isinstance(v, int):
        if v < 0:
            raise ValueError("Invalid snowflake ID: must be non-negative")
        _check_snowflake_upper_bound(v)
        return str(v)
    if isinstance(v, str):
        numeric = _parse_snowflake_string(v)
        return str(numeric)
    raise ValueError("Invalid snowflake ID type")


def snowflake_from_str(v: Any) -> Optional[int]:
    """Convert a canonical snowflake ID to int.

    Mirrors :class:`SnowflakeIDParam`: rejects ``bool`` and negatives and
    strips/normalizes strings, so the result always agrees with what the
    validators accept.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("Invalid snowflake ID")
    if isinstance(v, int):
        if v < 0:
            raise ValueError("Invalid snowflake ID: must be non-negative")
        _check_snowflake_upper_bound(v)
        return v
    if isinstance(v, str):
        numeric = _parse_snowflake_string(v)
        return numeric
    raise ValueError("Invalid snowflake ID type")


# Characters that break out of HTML attribute or text contexts. Rendered
# into client-facing UIs (roster, channel lists, server lists, emoji/sticker
# pickers, soundboards), so rejecting them at the input layer is defence in
# depth against stored XSS even when an output encoder is bypassed.
_UI_TEXT_HTML_META_CHARS = HTML_META_CHARS
# One shared policy object: the route and schema call sites below, the username
# rule in core/auth/passwords.py and the coverage test all resolve to this.
_UI_TEXT_POLICY = RejectUICharacters()


def _reject_ui_text_chars(value: str, field_name: str) -> str:
    """Reject HTML metacharacters and C0 control characters in UI-rendered text.

    The rule itself lives in ``src.core.security.policies`` so every call site
    shares one object that the coverage test can enumerate; the name stays
    because the routes and schemas below already import it. The exception is a
    ``TextRejected``, which is still a ``ValueError``, so the existing
    ``except ValueError`` handlers keep working.
    """
    return _UI_TEXT_POLICY.check(value, field_name)


def reject_unsafe_ui_text(value: str, field_name: str) -> str:
    """Stricter text rule for request schemas that echo text back to clients.

    ``_reject_ui_text_chars`` only covers HTML metacharacters and C0/DEL
    controls (the legacy rule shared with routes). It still lets C1 controls
    and Unicode bidirectional/invisible overrides through -- characters that
    reorder how a string renders without changing the stored bytes, i.e. a
    stored-display spoof no output encoder can undo. Request models use this
    helper so they also apply the shared text policy's unicode rule.

    Nothing is relaxed: the legacy check still runs first, then the unicode
    rule. Both raise ``ValueError`` subclasses, which Pydantic turns into a
    validation error (a 422 at the HTTP boundary).
    """
    checked = _reject_ui_text_chars(value, field_name)
    return reject_unsafe_unicode(checked, field_name)
