# Feedback Routes

Feedback endpoints support lightweight client or user feedback submission.

## Routes

- `POST /feedback`
- `GET /feedback/status`

## Purpose

- submit user feedback or product feedback payloads
- query the public availability or status of the feedback subsystem

## Client Guidance

- keep feedback payloads concise and user-generated
- avoid sending secrets, tokens, or private logs in feedback bodies
- use the status endpoint to disable UI affordances when feedback intake is unavailable

