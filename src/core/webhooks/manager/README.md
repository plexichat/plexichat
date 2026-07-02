# Webhook Manager

The webhook manager handles creating, managing, and executing webhooks with proper validation, permission checks, database interactions, and Ed25519 request signing for payload verification.

## Architecture

The manager uses a **mixin pattern** — each logical concern lives in its own class file. The final `WebhookManager` class in `composer.py` combines all mixins via multiple inheritance, following a linear MRO that ensures all methods resolve correctly regardless of which mixin calls which.

### Mixin Files

| File | Class | Responsibility |
|------|-------|----------------|
| `base.py` | `WebhookManagerTrait` | Shared trait: database helpers (`_get_channel`, `_get_server`, count helpers) and config loading. |
| `base.py` | `WebhookManagerBase` | Concrete base: `__init__` that accepts db/auth/messaging/servers/embeds modules and calls `_load_config()`. |
| `token.py` | `TokenMixin` | Token generation (`secrets.token_urlsafe`), SHA-256 hashing, constant-time verification, and `webhook.{id}.{secret}` format/parse. |
| `validation.py` | `ValidationMixin` | Name sanitization (strip HTML, reject `javascript:`), avatar URL validation (scheme, SSRF via `URLValidator`). |
| `permission.py` | `PermissionMixin` | `_check_manage_webhooks_permission` — delegates to the servers module's `has_permission` with `"webhooks.manage"`. |
| `crud.py` | `WebhookCRUDMixin` | Full CRUD: `create_webhook`, `get_webhook`, `get_webhook_by_token`, `get_channel_webhooks`, `get_server_webhooks`, `update_webhook`, `delete_webhook`, `regenerate_token`, `_row_to_webhook`. |
| `execute.py` | `WebhookExecutionMixin` | `execute_webhook` (send messages via the messaging module, with direct-DB fallback) and `execute_webhook_by_url`. |
| `signature.py` | `SignatureMixin` | `sign_payload` — decrypts the stored private key, signs a JSON payload with Ed25519, returns `X-Plexichat-Signature` / `X-Plexichat-Timestamp` headers. |
| `constants.py` | — | Shared constants (`WEBHOOK_NAME_MAX_LENGTH`, `TOKEN_BYTES`, `SIGNATURE_*`). |
| `composer.py` | `WebhookManager` | Empty body — just combines all base+mixins via MRO and delegates `__init__` to `WebhookManagerBase`. |

### How the Composer Assembles Them

```
WebhookManager
  → WebhookManagerBase     (__init__, _load_config, shared DB helpers)
  → TokenMixin             (_generate/_hash/_verify/_format/_parse token)
  → ValidationMixin        (_validate_name, _validate_avatar_url)
  → PermissionMixin        (_check_manage_webhooks_permission)
  → WebhookCRUDMixin       (create/get/update/delete/regenerate webhooks)
  → WebhookExecutionMixin  (execute_webhook, execute_webhook_by_url)
  → SignatureMixin         (sign_payload)
  → WebhookManagerTrait    (fallback: _get_channel/_get_server, counts, _load_config)
  → BaseManager            (from src.core.base)
```

All mixins inherit from `WebhookManagerTrait`, so every mixin can call any trait method through the composed MRO — no circular or duplicate inheritance issues.
