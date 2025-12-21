# API Schemas

Pydantic models for request validation and response serialization.

## Structure

| File | Description |
|------|-------------|
| `common.py` | Shared types (SnowflakeID, pagination) |
| `auth.py` | Authentication request/response models |
| `users.py` | User profile models |
| `servers.py` | Server and channel models |
| `channels.py` | Channel-specific models |
| `messages.py` | Message and attachment models |
| `reactions.py` | Reaction models |
| `relationships.py` | Friend/block models |
| `presence.py` | Presence/status models |
| `webhooks.py` | Webhook models |
| `version.py` | Version negotiation models |

## Common Types

### SnowflakeID

Custom type for snowflake IDs that serializes as strings:

```python
from src.api.schemas.common import SnowflakeID

class MyResponse(BaseModel):
    id: SnowflakeID  # Serializes as "123456789012345678"
```

### Pagination

Standard pagination parameters:

```python
from src.api.schemas.common import PaginationParams

@router.get("/items")
async def list_items(
    limit: int = Query(default=50, ge=1, le=100),
    before: Optional[str] = Query(default=None),
    after: Optional[str] = Query(default=None)
):
    pass
```

## Request Models

Request models use Pydantic for validation:

```python
from pydantic import BaseModel, Field

class CreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
```

## Response Models

Response models define the API contract:

```python
from pydantic import BaseModel
from src.api.schemas.common import SnowflakeID

class ItemResponse(BaseModel):
    id: SnowflakeID
    name: str
    created_at: int

    class Config:
        from_attributes = True
```

## Validation

Pydantic handles validation automatically:

```python
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    email: str = Field(..., pattern=r'^[\w\.-]+@[\w\.-]+\.\w+$')
    password: str = Field(..., min_length=8)
```

## Usage in Routes

```python
from src.api.schemas.auth import RegisterRequest, LoginResponse

@router.post("/register", response_model=LoginResponse)
async def register(body: RegisterRequest):
    # body is validated automatically
    pass
```
