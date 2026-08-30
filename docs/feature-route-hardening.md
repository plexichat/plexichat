# Feature-route hardening

This document describes the boundary validation and error-handling guarantees for the feature API (`src/api/routes/features/*:1`).

## IDs

- Every route that takes an ID uses `parse_id` (`src/api/routes/features/common.py:1`).
- The parser accepts only positive decimal strings. It rejects booleans, signs, zero, negatives, floats, and malformed values with `400` and the standard error envelope (`src/api/schemas/common.py:58`).

## Error handling

- Shared service adapters map validation and authorization failures to static, client-safe messages and log the diagnostic detail server-side. The client never receives raw exception text, SQL fragments, or file paths (`src/api/middleware/error_handling.py:84`).
- Feature-route failures use structured logging without leaking database or implementation details.
- Validation errors return `400` with `{"error":{"code":400,"message":"..."}}`. The API never returns `422` for validation — FastAPI validation errors are normalized to `400` (`src/api/middleware/error_handling.py:245`).

## Evidence URLs for reports

Enhanced-report evidence URLs (`src/api/routes/features/reports.py:1`) reject:

- URLs that contain credentials (`username` or `password` in the URL)
- Private, loopback, link-local, multicast, reserved, and unspecified IP destinations (checked after DNS resolution with `ipaddress` and `socket.getaddrinfo`)

The validation runs after URL parsing and before any fetch.

## Onboarding presets

Creating onboarding steps from a preset (`src/api/routes/features/onboarding.py:1`) can fail partway through. On failure the API returns a generic `Failed to create onboarding steps` error instead of raw exception text. The route logs the original error server-side.

## Voice messages

Voice metadata is validated in `src/api/routes/features/voice.py:40`:

- The storage URL must use `http` or `https`, must not contain credentials, and must not resolve to a private, loopback, link-local, multicast, reserved, or unspecified address.
- The only local URL forms accepted without DNS checks are the validated media prefixes `/api/v1/media/`, `/media/`, and `/uploads/`.
- Accepted content types: `audio/webm`, `audio/ogg`, `audio/mp3`, `audio/mpeg`, `audio/wav`, `audio/x-wav`, `audio/opus`. Extensions are checked against `webm`, `ogg`, `mp3`, `wav`, `opus`.
- Size must be greater than 0 and at most 10 MB. Duration must be greater than 0 and at most 600,000 ms (10 minutes).
- The JSON variant (`POST /voice-messages`) and the multipart variant (`POST /voice-messages/upload`) apply the same checks.

## Audit logs and reports

- Audit-log and report projections (`src/api/routes/features/audit_logs.py:1`, `src/api/routes/features/reports.py:1`) support both mapping and tuple-backed database adapters. Tuple rows use explicit index maps so column reordering does not produce wrong fields.

## Tests

Regression coverage is in `src/tests/features/test_next_findings_security.py`. The focused feature and static-client suites pass with 41 tests.
