"""
Common schemas - Shared Pydantic models.
"""

from typing import Optional, List, Any, Generic, TypeVar
from pydantic import BaseModel, Field, ConfigDict, GetCoreSchemaHandler
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
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.union_schema([
                core_schema.int_schema(),
                core_schema.str_schema(),
            ]),
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def _validate(cls, v: Any) -> str:
        """Validate and convert to string."""
        if isinstance(v, int):
            return cls(str(v))
        if isinstance(v, str):
            # Validate it's a valid number string
            try:
                int(v)
                return cls(v)
            except ValueError:
                raise ValueError("Invalid snowflake ID: must be numeric string")
        raise ValueError(f"Invalid snowflake ID type: {type(v)}")


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

    limit: int = Field(default=50, ge=1, le=100, description="Number of items to return")
    before: Optional[SnowflakeID] = Field(default=None, description="Get items before this ID")
    after: Optional[SnowflakeID] = Field(default=None, description="Get items after this ID")


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""
    model_config = ConfigDict(from_attributes=True)

    items: List[T]
    has_more: bool = False
    total: Optional[int] = None


def snowflake_to_str(v: Any) -> Optional[str]:
    """Convert snowflake ID to string."""
    if v is None:
        return None
    return str(v)


def snowflake_from_str(v: Any) -> Optional[int]:
    """Convert string to snowflake ID."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            raise ValueError("Invalid snowflake ID")
    raise ValueError("Invalid snowflake ID type")
