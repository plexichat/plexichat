# Users router

Manage the current user, discover other users, and manage direct-message (DM) channels.

The `users` package splits the `UsersRouter` implementation into focused mixin classes. The composer combines the mixins with multiple inheritance and registers the routes on a single FastAPI `APIRouter`.

## Architecture

```
users/
├── __init__.py   # Creates APIRouter(tags=["Users"]), instantiates UsersRouter, calls register_routes()
├── base.py       # UsersRouterBase — shared helpers such as cache invalidation
├── profile.py    # ProfileMixin — GET/PATCH /@me, GET /{user_id}, DELETE /@me
├── avatar.py     # AvatarMixin — POST /@me/avatar
├── channel.py    # ChannelMixin — GET /@me/notes, GET/POST /@me/channels
├── discovery.py  # DiscoveryMixin — GET /search
├── settings.py   # SettingsMixin — GET/PATCH /@me/messaging-settings
├── dsar.py       # DataExportMixin — POST/GET /@me/data-export, GET/DELETE /@me/data-export/{request_id}, GET /@me/data-export/{request_id}/download
├── composer.py   # UsersRouter — combines all mixins, register_routes()
├── helpers.py    # Shared helpers such as _get_user_cached() and _user_to_public_response()
└── README.md     # This file
```

## Inheritance order

`UsersRouter` uses Python method resolution order (MRO):

```
UsersRouter
  → ProfileMixin
  → AvatarMixin
  → ChannelMixin
  → DiscoveryMixin
  → SettingsMixin
  → DataExportMixin
  → UsersRouterBase
```

## Endpoints

All paths are mounted under the users prefix and require authentication through `get_current_user`. Unless noted, the router returns `ErrorResponse` with `401 Not authenticated` when the token is missing or invalid.

| Method | Path | Handler | Success response | Notes |
|--------|------|---------|------------------|-------|
| GET | `/@me` | `get_current_user_info` | `UserResponse` | Cached `current_user_api` 60s. Returns a synthetic bot response when `token_type == "bot"`. |
| PATCH | `/@me` | `update_current_user` | `UserResponse` | Requires `current_password` when you change `password` or `email`. Profile fields (`bio`, `pronouns`, `location`, `timezone`, `banner_url`, `social_links`) go to `ProfileManager`. `avatar_url` must equal `/api/v1/avatars/users/{user_id}` and requires an uploaded avatar row. Emits `emit_user_update` after success. |
| POST | `/@me/avatar` | `upload_avatar` | `UserAvatarResponse` | Uploads the avatar file; use this endpoint instead of setting `avatar_url` directly. |
| GET | `/@me/notes` | `get_notes_channel` | `NotesChannelResponse` | `channel_type` is always `"notes"`. Returns `501 Notes not implemented` if the messaging backend does not expose `get_or_create_notes`. |
| GET | `/@me/channels` | `get_dm_channels` | `List[DMChannelResponse]` | Cached `user_dm_channels_api` 5s. Filters to `conversation_type == "dm"`. `channel_type` on each item is `"dm"`. |
| POST | `/@me/channels` | `create_dm_channel` | `DMChannelResponse` | Body: `DMChannelCreateRequest` (`recipient_id`). Rejects self-DMs with `400`. `channel_type` is `"dm"`. Returns `403 Cannot message this user` for `ConversationAccessDeniedError` and `404 User not found` for `InvalidRecipientError` / `ConversationNotFoundError`. |
| GET | `/search` | `search_user_by_username` | `UserPublicResponse` | Query by username. Returns `400 Username required` when the query is empty. |
| GET | `/{user_id}` | `get_user` | `UserPublicResponse` | Validates `user_id` as int64. Cache prefix `user_profile_api` 60s. |
| GET | `/@me/messaging-settings` | `get_messaging_settings` | `MessagingSettingsResponse` | |
| PATCH | `/@me/messaging-settings` | `update_messaging_settings` | `MessagingSettingsResponse` | Body: `MessagingSettingsUpdateRequest`. |
| DELETE | `/@me` | `schedule_account_deletion` | `SuccessResponse` | Body: `UserDeletionRequest` (`password`, optional `code` when 2FA is enabled). Returns `400 Invalid password or 2FA code`. Starts a 30-day grace period. |
| POST | `/@me/data-export` | `request_data_export` | `SuccessResponse` | Body: `DSARRequestBody` (`password`, optional `format` `"json"` or `"zip"` default `"json"`). Verifies password against `auth_users.password_hash`. Rate limited: 1 request per 24 hours (`429 Data export request limit exceeded`). |
| GET | `/@me/data-export` | `get_data_export_requests` | `DSARRequestListResponse` | Lists all export requests for the current user. |
| GET | `/@me/data-export/{request_id}` | `get_data_export_status` | `DSARRequestResponse` | Returns `400 Invalid request ID` for non-numeric IDs and `404 Request not found` for other users' IDs. |
| DELETE | `/@me/data-export/{request_id}` | `cancel_data_export_request` | `SuccessResponse` | Allowed only when `status` is `pending`, `approved`, or `generating`. Otherwise `400 Cannot cancel this request`. |
| GET | `/@me/data-export/{request_id}/download` | `download_data_export` | `StreamingResponse` | Allowed only when `status` is `ready` or `downloaded`. Returns `410 Download link has expired` when `expires_at` is in the past and `404 Export file not found` when storage is missing. Streams with `Content-Disposition`, `X-DSAR-Checksum`, and `X-DSAR-Request-Id` headers; marks the export `downloaded` only after the stream completes. |

## Channel types

Use `channel_type` to distinguish channel kinds:

* `dm` — direct-message channel (`DMChannelResponse`). Includes `recipient_id`, `recipient` (`RecipientResponse`), and `last_message_id`.
* `notes` — personal notes channel (`NotesChannelResponse`). Includes `name` (`Personal Notes`), `last_message_id`, and `last_message_at`.
* `text`, `voice`, `category` — server channels (`ChannelResponse` in `src/api/schemas/servers.py`). See `src/api/schemas/channels.py:ChannelCreateRequest` for creation fields.

Do not confuse `channel_type` with `conversation_type` on the messaging backend; the route layer filters conversations where `conversation_type == "dm"`.

## Key design decisions

* **Method-based handlers.** Module-level functions become instance methods so mixins share state through `self`.
* **Explicit registration.** `register_routes()` calls `router.get(...)()`, `router.post(...)()`, and so on, instead of decorators. All OpenAPI metadata (`response_model`, `summary`, `responses`) remains in `composer.py`.
* **Module-level router.** `__init__.py` creates `router = APIRouter(tags=["Users"])`, instantiates `UsersRouter()`, and calls `register_routes(router)`. The `router` object is the only public export.
* **Cached handlers.** `@cached(ttl=60, prefix="current_user_api")` and similar decorators stay on mixin methods. They run before route registration and behave identically to the original double-decorator pattern.
* **Rate limiting.** DSAR export creation uses `check_rate_limit("dsar:user:{user_id}", limit=1, window_seconds=86400)` in `dsar.py:75`. When the limit is exceeded, the endpoint returns `429`.

## Add a new route

1. Add the handler method to the appropriate mixin file, or create a new mixin file if the route belongs to a new category.
2. Register the route in `composer.py:register_routes()`:

   ```python
   router.get(
       "/@me/new-feature",
       response_model=MyResponse,
       summary="Describe the feature",
       responses={401: {"model": ErrorResponse, "description": "Not authenticated"}},
   )(self.my_handler)
   ```

3. If you add a new mixin, include it in the `UsersRouter` class definition and place it before `UsersRouterBase` in the MRO.

## Error handling

All handlers raise `HTTPException` with `detail={"error": {"code": <status>, "message": "<message>"}}`. The router preserves original `HTTPException` instances and logs unexpected exceptions before returning `500 Internal server error`. The health endpoints (`/health`, `/ready`) are not part of this router; see `src/api/routes/health.py`.
