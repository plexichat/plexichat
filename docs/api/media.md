# Media API

Use the Media API to report content, manage resumable uploads, check compression status, serve attachments, and proxy external images.

**Base URL**

`https://api.plexichat.com`

For development, use `http://localhost:8000`.

All paths in this document are prefixed with `/api/v1` unless noted. All endpoints require authentication with `Authorization: Bearer <token>`.

## Authentication

Include an `Authorization: Bearer <token>` header on every request.

Common error responses:

| Code | Condition |
|------|-----------|
| 401 | Invalid or expired token |
| 500 | Internal server error |

## Report a content hash

### `POST /media/report`

Report a file hash for moderation review. Multiple reports on the same hash can trigger automatic blocking. The server accepts SHA-256 and perceptual hashes.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `hash_value` | string | Required | SHA-256 or perceptual hash. 16-128 characters. |
| `reason` | string | Required | Reason for the report. 1-500 characters. |
| `details` | string | Optional | Additional details. Up to 2000 characters. |
| `phash_value` | string | Optional | Perceptual hash for images. Up to 64 characters. |
| `uploader_id` | string (Snowflake ID) | Optional | User ID of the uploader. |
| `message_id` | string (Snowflake ID) | Optional | Message ID that contains the attachment. |
| `attachment_url` | string | Optional | URL of the attachment. Up to 2000 characters. |
| `block_uploader` | boolean | Optional | Request to block the uploader. Default `false`. |

**Response `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the report was submitted. |
| `report_id` | string | Generated report ID. |
| `message` | string | Status message. |

## Check hash status

### `GET /media/hash/{hash_value}/status`

Check whether a hash is blocked.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `hash_value` | string | Hash to check. |

**Response `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `hash_value` | string | The hash that was checked. |
| `is_blocked` | boolean | Whether the hash is blocked. |
| `reason` | string or null | Reason for blocking, if blocked. |

## Create an upload session

### `POST /media/upload/session`

Create a chunked upload session for large files. The response includes the session ID, chunk size, and expiry.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `filename` | string | Required | Original filename. 1-255 characters. |
| `content_type` | string | Required | MIME type. 1-100 characters. |
| `total_size` | integer | Required | Total file size in bytes. Must be `> 0` and `<= 1073741824` (1 GiB). |

**Response `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Opaque upload session ID. |
| `chunk_size` | integer | Expected chunk size in bytes. |
| `total_chunks` | integer | Total number of chunks. |
| `expires_at` | integer | Session expiration as Unix timestamp. |

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid request or file too large |

## Upload a chunk

### `POST /media/upload/chunk/{session_id}`

Upload a chunk to an existing session. Use `multipart/form-data` with the chunk as the `file` field.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `session_id` | string | Upload session ID. |

**Query parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `chunk_index` | integer | Required | Zero-based index of this chunk. |
| `chunk_checksum` | string | Optional | SHA-256 checksum (lowercase hex) for verification. |

**Request**

Send the chunk bytes as `file` in `multipart/form-data`.

**Response `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the chunk was stored. |
| `chunk_index` | integer | Index of the uploaded chunk. |
| `uploaded_chunks` | integer | Number of chunks uploaded so far. |
| `total_chunks` | integer | Total chunks in the session. |
| `progress_percent` | number | Overall upload progress percentage. |
| `is_complete` | boolean | Whether all chunks have been uploaded. |
| `error` | string or null | Error message if the upload failed. |

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid chunk or session |

## Complete an upload session

### `POST /media/upload/complete/{session_id}`

Complete a chunked upload session after all chunks are uploaded. The server assembles the file and records it.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `session_id` | string | Upload session ID. |

**Response `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Whether the upload was completed. |
| `size` | integer | Total size of the assembled file. |
| `message` | string | Status message. Example: `Upload complete. File ready for processing.` |

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Session not complete or not found |

## List upload sessions

### `GET /media/upload/sessions`

List all active upload sessions for the authenticated user.

**Response `200 OK`**

```json
{
  "sessions": [
    {
      "session_id": "abc123",
      "filename": "video.mp4",
      "content_type": "video/mp4",
      "total_size": 104857600,
      "uploaded_chunks": 5,
      "total_chunks": 10,
      "progress_percent": 50.0,
      "status": "uploading",
      "expires_at": 1704067200
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `sessions` | array | List of `UploadSessionInfo` objects. |

`UploadSessionInfo` fields: `session_id`, `filename`, `content_type`, `total_size`, `uploaded_chunks`, `total_chunks`, `progress_percent`, `status` (`pending`, `uploading`, `complete`, `error`), `expires_at`.

## Cancel an upload session

### `DELETE /media/upload/session/{session_id}`

Cancel an upload session and clean up stored chunks.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `session_id` | string | Upload session ID. |

**Response `200 OK`**

```json
{ "success": true }
```

**Error responses**

| Code | Condition |
|------|-----------|
| 404 | Session not found |

## Get compression status

### `GET /media/compression/status`

Get the server's compression capabilities.

**Response `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | boolean | Whether compression is enabled globally. |
| `image_compression` | boolean | Whether image compression is enabled. |
| `video_compression` | boolean | Whether video compression is enabled. |
| `pillow_available` | boolean | Whether Pillow is installed. |
| `ffmpeg_available` | boolean | Whether ffmpeg is installed. |
| `ffprobe_available` | boolean | Whether ffprobe is installed. |

## Proxy external content

### `GET /media/proxy`

Fetch and serve an external image through the server. The server fetches the URL, validates SSRF and DNS rebinding, enforces an image allow-list, rejects SVG and magic-byte mismatches, and caches the result. The proxy prevents the origin from learning the client's address.

**Query parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `url` | string | Required | External URL to proxy. Must use `http` or `https`. Up to 2048 characters. |

**Behavior**

- Only `image/jpeg`, `image/png`, `image/gif`, and `image/webp` are returned. Other content types are rejected.
- The server enforces a per-user rate limit. Default is 120 requests per 60 seconds. Excess requests return `429`.
- Responses are cached by the media proxy.

**Response `200 OK`**

The raw image bytes with the appropriate `Content-Type` (`image/jpeg`, `image/png`, `image/gif`, `image/webp`).

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid, blocked, or non-image URL. Also returned on fetch failure or content-type rejection. |
| 401 | Invalid or expired token |
| 404 | Proxy not available |
| 429 | Too many proxied media requests; retry later |
| 500 | Internal server error |

**Example**

```bash
curl -X GET "http://localhost:8000/api/v1/media/proxy?url=https%3A%2F%2Fexample.com%2Fimage.jpg" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

## Serve an attachment

### `GET /api/v1/media/attachments/{filename}`

Serve an uploaded attachment file. Supports local and S3 storage, range requests, and conditional requests.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `filename` | string | Attachment filename. Must be a single path segment; `..`, `/`, and `\` are rejected with `400`. |

**Authentication**

The request succeeds with any of the following:

- A valid `Authorization: Bearer <token>` header.
- A valid `plexichat_token` cookie (used by `<img>` tags).
- A valid signed URL query string (`uid` + signature). Signed URLs are verified with `verify_signed_url` and still enforce per-file access checks when a `uid` is present.
- An internal service request (`is_internal`).

If none of these is present, the server returns `401`.

**Authorization**

After authentication, the server checks `check_file_access(filename, user_id)` unless the caller is an admin session or internal service. Requests without file access return `403`.

**File states**

| `scan_status` | Result |
|---------------|--------|
| `pending` | `403 File not available` |
| `infected` | `403 File blocked by security scan` |
| deleted or missing | `404 File not found` (cache is invalidated) |

**Range and caching**

- Range requests use `Range: bytes=start-end` and return `206` with `Content-Range` when the underlying stream is seekable.
- `If-None-Match` is checked against `ETag: "file_id-size"` and returns `304` on match.
- `Cache-Control: private, max-age=3600` is set.

**Response `200 OK` or `206 Partial Content`**

The file bytes. Content type comes from stored metadata. `Content-Disposition` is `inline` for `image/*`, `video/*`, and `audio/*` (except `image/svg*`), otherwise `attachment; filename="..."`. Use `?download=1` to force `attachment`.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid filename |
| 401 | Authentication required or invalid token |
| 403 | Access denied or file not yet available |
| 404 | File not found or deleted |
| 500 | Retrieval failed or internal error |

## Attachment upload performance

`POST /api/v1/channels/{id}/attachments` stores the file and database metadata, then returns immediately. Thumbnails are generated asynchronously. Clients should poll message or attachment responses for thumbnail availability. Tiny images smaller than the smallest thumbnail (64 px by default) skip thumbnail generation.

## Client guidance

- Use the session flow for large or unreliable uploads.
- Retry individual chunks by index instead of restarting the session.
- Treat server-generated `session_id` values as opaque.
- Track chunk indexes and checksums on the client so resumed uploads stay consistent.
- Check `is_complete` after each chunk and call `POST /media/upload/complete/{session_id}` only when all chunks are uploaded.
- For the proxy, URL-encode the `url` parameter and handle `400` and `429` before retrying.
