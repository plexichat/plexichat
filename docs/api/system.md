# System And Utility Routes

Public utility endpoints for clients and operators.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are prefixed with `/api/v1/` unless otherwise specified.

## Routes

- `GET /api/v1/health`
- `GET /api/v1/version`
- `POST /api/v1/version/negotiate`
- `GET /api/v1/status`
- `GET /api/v1/capabilities`
- `GET /api/v1/help/security-logout`
- `GET /api/v1/help/access-blocked`
- `GET /api/v1/qr`
- `GET /qr`

## Purpose

- runtime health and readiness checks
- version and compatibility negotiation
- maintenance-mode or availability checks
- capability discovery for public constants and access-token policy
- small help pages served by the backend
- QR code generation for client flows that need it

## Client Guidance

- use `version` and `version/negotiate` before relying on new client features
- use `capabilities` for runtime feature hints instead of hardcoding assumptions
- use `status` and `health` for lightweight diagnostics
- treat help content as human-facing supplemental guidance, not machine API schema
- call `/qr` with `data`, and optionally `size` and `format`, when you need a locally generated QR image

