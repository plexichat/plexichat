# Media Routes

The media routes cover moderation reporting, upload-session management, compression visibility, and attachment serving.

## Routes

- `POST /media/report`
- `GET /media/hash/{hash_value}/status`
- `POST /media/upload/session`
- `POST /media/upload/chunk/{session_id}`
- `POST /media/upload/complete/{session_id}`
- `GET /media/upload/sessions`
- `DELETE /media/upload/session/{session_id}`
- `GET /media/compression/status`
- `GET /api/v1/media/attachments/{filename}`

## Purpose

- report a file hash for moderation review
- check whether a known hash is blocked before reuse
- create resumable upload sessions
- upload chunks and finalize attachments
- inspect or cancel in-progress uploads
- check the state of background compression work
- serve uploaded attachment files with auth checks

## Attachment Upload Performance

The attachment upload endpoint (`POST /api/v1/channels/{id}/attachments`) returns immediately
after storing the file and recording database metadata. Thumbnails are generated asynchronously
in the background to keep upload latency low. Clients should not expect thumbnails to be available
in the upload response — they will appear in subsequent message/attachment responses once
background processing completes. Tiny images (smaller than the smallest thumbnail size, 64px by
default) skip thumbnail generation entirely.

## Client Guidance

- use the session flow for large or unreliable uploads
- retry individual chunks carefully instead of restarting everything blindly
- treat server-generated upload identifiers as opaque values
- track chunk indexes and checksums on the client so resumed uploads stay consistent
- thumbnails are generated asynchronously after upload; poll the message response to check availability

