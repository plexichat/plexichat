# API dependencies

The `dependencies.py` module provides dependency injection utilities for
the Plexichat API layer. It re-exports commonly used dependencies so that
route handlers can import them from a single location.

## Available dependencies

| Dependency | Description |
|------------|-------------|
| `get_db` | Returns the database instance |
| `get_auth` | Returns the authentication module |
| `get_current_user` | Extracts and validates the authenticated user from the request |
| `get_optional_user` | Like `get_current_user`, but returns `None` when no credentials are provided |

## Usage

Use these dependencies in FastAPI route declarations:

```python
from fastapi import APIRouter, Depends
from src.api.dependencies import get_db, get_current_user

router = APIRouter()

@router.get("/example")
async def example(db=Depends(get_db), user=Depends(get_current_user)):
    ...
```

The `get_current_user` and `get_optional_user` functions are defined in
`src.api.middleware.authentication` and re-exported from this module for
convenience.
