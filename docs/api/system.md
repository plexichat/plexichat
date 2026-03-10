# System And Utility Routes

This page collects the small public utility route groups that are useful to clients and operators.

## Routes

- `GET /health`
- `GET /version`
- `POST /version/negotiate`
- `GET /status`
- `GET /capabilities`
- `GET /help/security-logout`
- `GET /help/access-blocked`
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

