# OAuth2

OAuth2 authorization for third-party applications.

## Components

- `scopes.py` - Scope definitions and validation
- `tokens.py` - Token generation and verification
- `flows.py` - OAuth2Flow implementation

## Usage

```python
from src.core.applications.oauth import (
    validate_scopes,
    generate_access_token,
    OAuth2Flow,
)

flow = OAuth2Flow()
code = flow.create_authorization_code(client_id, user_id, scopes)
tokens = flow.exchange_code(code)
```

## Supported Flows

- Authorization Code Grant
- Token refresh
