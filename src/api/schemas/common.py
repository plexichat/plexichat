"""
Common schemas - Shared Pydantic models.
"""

from typing import Optional, List, Any, Generic, TypeVar
from pydantic import BaseModel, Field, field_validator, ConfigDict

T = TypeVar("T")


class SnowflakeID(str):
    """Snowflake ID represented as string for JSON serialization."""
    
    @classmethod
    def __get_validators__(cls):
        yield cls.validate
    
    @classmethod
    def validate(cls, v):
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            try:
                int(v)
                return v
            except ValueError:
                raise ValueError("Invalid snowflake ID")
        raise ValueError("Invalid snowflake ID type")


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
    before: Optional[str] = Field(default=None, description="Get items before this ID")
    after: Optional[str] = Field(default=None, description="Get items after this ID")


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response wrapper."""
    model_config = ConfigDict(from_attributes=True)
    
    items: List[T]
    has_more: bool = False
    total: Optional[int] = None


def snowflake_to_str(v: Any) -> str:
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
