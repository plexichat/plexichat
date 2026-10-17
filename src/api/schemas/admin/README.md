# Admin schemas

Define Pydantic models for admin API operations. The package splits the former monolithic `admin.py` into domain-specific modules. Each module groups related request and response schemas.

## Structure

| Module | Contents | Schemas |
|--------|----------|---------|
| `admin_auth.py` | Admin login, OTP verification, password change, security status, OTP setup/disable, backup codes | `AdminLoginRequest`, `AdminLoginResponse`, `OTPVerifyRequest`, `AdminChangePasswordRequest`, `AdminSecurityStatusResponse`, `AdminOTPSetupBeginRequest`, `AdminOTPDisableRequest`, `AdminBackupCodesResponse` |
| `tickets.py` | Support ticket status updates and internal notes | `TicketStatusUpdate`, `InternalNoteCreate`, `TicketResponse`, `NoteResponse` |
| `telemetry.py` | Endpoint stats, system metrics, dashboard, history buckets, export, reset | `TelemetryEndpointStat`, `SystemMetrics`, `AdminDashboardResponse`, `TelemetryStatsResponse`, `TelemetryHistoryBucket`, `TelemetryHistoryResponse`, `TelemetryResetResponse`, `TelemetryExportResponse` |
| `hash_reports.py` | Image hash report review, manual block, blocked hash info | `HashReportReviewRequest`, `ManualBlockHashRequest`, `BlockedHashResponse`, `HashReportResponse`, `HashReportCountsResponse`, `HashReportReviewResponse`, `BlockHashResponse` |
| `moderation_reports.py` | Message and user report review, report counts, user blocking | `ModerationReportCountsResponse`, `ModerationReportReviewRequest`, `ModerationReportReviewResponse`, `MessageReportResponse`, `UserReportResponse`, `BlockUserRequest`, `BlockedUserResponse`, `BlockUserResponse` |
| `user_management.py` | Search, detail, tier, badges, banned usernames, notes, scheduling, force actions | `UserTierUpdate`, `UserSearchResponse`, `UserSearchListResponse`, `UserDetailsResponse`, `BannedUsernameResponse`, `BannedUsernameCreate`, `UserNotesResponse`, `UserNotesUpdate`, `UserTierUpdateResponse`, `UserBadgeUpdateResponse`, `ScheduledDeletionResponse`, `ScheduledDeletionListResponse`, `ForceLogoutRequest`, `ForceUsernameChangeRequest`, `UserLockRequest`, `AvailableTierInfo`, `AvailableTiersResponse`, `AvailableBadgesResponse` |
| `ip_management.py` | IP block and unblock | `IPBlockRequest`, `BlockedIPResponse` |
| `access_tokens.py` | Token CRUD, rotation, scopes, usage events, top IPs/paths, detail view | `AccessTokenCreateRequest`, `AccessTokenResponse`, `AccessTokenCreateResponse`, `AccessTokenUpdateRequest`, `AccessTokenRotateRequest`, `AccessTokenScopeCreateRequest`, `AccessTokenScopeResponse`, `AccessTokenUsageIPResponse`, `AccessTokenUsagePathResponse`, `AccessTokenUsageEventResponse`, `AccessTokenDetailResponse` |
| `logs.py` | Log files, log lines, paginated view | `LogFileInfo`, `LogLine`, `LogViewResponse` |
| `automod.py` | Auto-moderation rules and global config | `AutomodRuleAction`, `AutomodRuleResponse`, `AutomodRuleCreateRequest`, `AutomodRuleUpdateRequest`, `AutomodConfigResponse`, `AutomodConfigUpdateRequest` |
| `__init__.py` | Re-exports all schemas for backward compatibility | — |

Total: 75 schemas across 10 domain modules (plus `__init__.py`).

## Import

Import from the package root so existing code continues to work. The old `admin.py` has been removed.

```python
from src.api.schemas.admin import AdminLoginRequest, TelemetryStatsResponse
from src.api.schemas.admin import AccessTokenDetailResponse, LogViewResponse
```

You can also import directly from a domain module when you want a narrower dependency:

```python
from src.api.schemas.admin.telemetry import TelemetryHistoryResponse
from src.api.schemas.admin.access_tokens import AccessTokenCreateRequest
```

## Design principles

* **Self-contained modules.** Each module imports only from `pydantic` and the standard library. No schema module imports from another schema module.
* **Consistent model config.** Every schema sets `model_config = ConfigDict(from_attributes=True)` and uses explicit `Field(...)` descriptors where validation is needed.
* **No mixins.** Schemas are plain `BaseModel` subclasses. The package uses module-level grouping, not class inheritance (unlike the `users` router mixins).

## Field reference

### Channel types and read receipts

Admin schemas do not define `channel_type` or `read_by`. Those fields belong to user-facing schemas:

* `channel_type` — see `src/api/schemas/channels.py` (`dm`, `notes`) and `src/api/schemas/servers.py` (`text`, `voice`, `category`). Admin telemetry and moderation schemas reference channel IDs but do not redefine the enum.
* `read_by` — `List[ReaderInfo]` on `src/api/schemas/messages.py:MessageResponse`. Each reader has `id`, `username`, and optional `avatar_url`. Admin views expose `read_count` aggregates, not per-reader lists, unless you query message details.

### Rate limits and health

* **Rate limits** are enforced in route handlers, not in schemas. For example, `POST /users/@me/data-export` allows one request per 24 hours and returns `429` when exceeded. Admin token and IP management endpoints do not impose a schema-level rate limit.
* **Health checks** are not admin schemas. Use `GET /health` (detailed `HealthResponse` with `status`, `version`, `events_metrics`, `cross_worker_listener`) and `GET /ready` (minimal `{"status": "ready"}`) from `src/api/routes/health.py`. The admin dashboard fetches these for the status badge and worker-health card but does not validate them through an admin schema.

### Access tokens

`AccessTokenResponse` includes `scope_mode` (`none` | `monitor` | `enforce`), `use_count_total`, `distinct_ip_count`, `denied_count_total`, and revocation fields. `AccessTokenDetailResponse` bundles the token with `scopes`, `recent_events`, `top_ips`, `top_paths`, `total_events`, and `distinct_ip_count`. Scope creation requires `scope_type` (`ip` | `cidr`) and a `value` (see `src/api/schemas/admin/access_tokens.py:75`).

### Logs and telemetry

`LogViewResponse` is a paginated view of `LogLine` entries for a given `LogFileInfo`. `TelemetryStatsResponse` and `TelemetryHistoryResponse` power the endpoint-performance table and history chart on the admin dashboard. History queries accept `endpoint`, `method` (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`), `hours` (`1`, `6`, `24`, `168`), and `bucket` (`1`, `5`, `15`, `60` minutes).

## Add a new admin schema

1. Create or edit the appropriate domain module (for example, `user_management.py` for user-related fields).
2. Define the `BaseModel` with `model_config = ConfigDict(from_attributes=True)` and `Field` metadata.
3. Re-export the new schema from `src/api/schemas/admin/__init__.py` so `from src.api.schemas.admin import NewSchema` works.

Example:

```python
from pydantic import BaseModel, Field, ConfigDict

class MyAdminRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    reason: str = Field(..., max_length=512, description="Reason for the action")
```
