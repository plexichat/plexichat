# Media Routes

The media routes cover moderation reporting, upload-session management, and compression visibility.

## Routes

- `POST /media/report`
- `GET /media/hash/{hash_value}/status`
- `POST /media/upload/session`
- `POST /media/upload/chunk/{session_id}`
- `POST /media/upload/complete/{session_id}`
- `GET /media/upload/sessions`
- `DELETE /media/upload/session/{session_id}`
- `GET /media/compression/status`

## Purpose

- report a file hash for moderation review
- check whether a known hash is blocked before reuse
- create resumable upload sessions
- upload chunks and finalize attachments
- inspect or cancel in-progress uploads
- check the state of background compression work

## Client Guidance

- use the session flow for large or unreliable uploads
- retry individual chunks carefully instead of restarting everything blindly
- treat server-generated upload identifiers as opaque values
- track chunk indexes and checksums on the client so resumed uploads stay consistent

