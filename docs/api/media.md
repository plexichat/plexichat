# Media Routes

The media routes cover upload safety checks, upload-session management, and compression visibility.

## Routes

- `POST /media/hash-report`
- `POST /media/upload/initiate`
- `PUT /media/upload/session/{session_id}/chunk/{chunk_number}`
- `POST /media/upload/session/{session_id}/finalize`
- `GET /media/upload/session/{session_id}`
- `GET /media/upload/sessions`
- `DELETE /media/upload/session/{session_id}`
- `GET /media/compression/status`

## Purpose

- report or evaluate file hashes before upload
- create resumable upload sessions
- upload chunks and finalize attachments
- inspect or cancel in-progress uploads
- check the state of background compression work

## Client Guidance

- use the session flow for large or unreliable uploads
- retry individual chunks carefully instead of restarting everything blindly
- inspect session state before resuming interrupted uploads
- treat server-generated upload identifiers as opaque values

