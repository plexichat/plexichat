# Admin Schemas

Pydantic models for admin API operations. Refactored from a monolithic `admin.py` into domain-specific modules.

## Structure

| Module | Contents | Count |
|--------|----------|-------|
| `admin_auth.py` | Login, OTP, password change, security status | 8 |
| `tickets.py` | Support ticket CRUD and internal notes | 4 |
| `telemetry.py` | Endpoint stats, system metrics, dashboard, history, export | 8 |
| `hash_reports.py` | Hash review, manual block, blocked hash info | 7 |
| `moderation_reports.py` | Message/user report review, user blocking | 8 |
| `user_management.py` | Search, details, tier, badges, banned usernames, notes, scheduling, force actions | 18 |
| `ip_management.py` | IP block/unblock | 2 |
| `access_tokens.py` | Token CRUD, scopes, usage events, detail views | 11 |
| `logs.py` | File info, log lines, paginated view | 3 |
| `automod.py` | Auto-moderation rules and config | 6 |
| `__init__.py` | Re-exports all schemas for backward compatibility | — |

Total: **75 schemas** across 10 domain modules.

## Backward Compatibility

All schemas are re-exported from `__init__.py`, so existing imports like:

```python
from src.api.schemas.admin import AdminLoginRequest, TelemetryStatsResponse
```

continue to work unchanged. The old `admin.py` has been removed.

## Design Principles

- **Self-contained modules**: Each module imports only what it needs from `pydantic` and the standard library.
- **No cross-module dependencies**: Schema modules do not import from each other to keep the dependency graph flat.
- **Consistent pattern**: Every schema uses `model_config = ConfigDict(from_attributes=True)` and explicit `Field(...)` descriptors where applicable.
- **No mixins**: Unlike the `presence/manager/` pattern, schemas are simple Pydantic models — composition is through module-level grouping, not class inheritance.
