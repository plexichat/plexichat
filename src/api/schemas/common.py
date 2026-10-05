"""
Common schemas - Shared Pydantic models.
"""

from typing import Optional, List, Any, Generic, TypeVar, Annotated
from pydantic import BaseModel, BeforeValidator, Field, ConfigDict, GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

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
            return cls(str(v))
        if isinstance(v, str):
            text = v.strip()
            try:
                numeric = int(text)
            except ValueError:
                raise ValueError("Invalid snowflake ID: must be numeric string")
            if numeric < 0:
                raise ValueError("Invalid snowflake ID: must be non-negative")
            return cls(text)
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
            return v
        if isinstance(v, str):
            try:
                numeric = int(v.strip())
            except ValueError:
                raise ValueError("Invalid snowflake ID: must be numeric string")
            if numeric < 0:
                raise ValueError("Invalid snowflake ID: must be non-negative")
            return numeric
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
    """Convert snowflake ID to string."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("Invalid snowflake ID")
    return str(v)


def snowflake_from_str(v: Any) -> Optional[int]:
    """Convert string to snowflake ID."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("Invalid snowflake ID")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            raise ValueError("Invalid snowflake ID")
    raise ValueError("Invalid snowflake ID type")
