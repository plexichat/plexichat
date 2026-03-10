# Telemetry Routes

Telemetry endpoints ingest client-side operational signals.

## Routes

- `POST /telemetry/response-times`
- `POST /telemetry/csp-report`

## Purpose

- record batches of client-observed response-time metrics
- submit Content Security Policy violation reports

## Notes

These routes are intended for controlled telemetry payloads, not arbitrary analytics dumping.

## Client Guidance

- send only the fields the backend documents or expects
- avoid including secrets, tokens, or personal message content in telemetry
- treat telemetry delivery as best effort and non-blocking

