# Media tests

Tests for media upload, processing, storage, signing, proxying, and integration with
messaging.

## Test files

- `test_edge_cases.py` — Covers oversized files, disallowed content types, special-character filenames, media-type detection, duplicate uploads, and text-file handling.
- `test_images.py` — Tests image metadata creation, thumbnail generation, resizing,
  format conversion, and content-type detection for PNG, JPEG, GIF, and WebP.
- `test_integration.py` — Validates upload-and-attach workflows with messaging, rate-limit status, file scanning, video metadata handling, and multi-file uploads.
- `test_proxy.py` — Tests the external proxy SSRF protections: HTTPS/SNI behavior,
  redirect rejection, port validation, SVG sniffing, magic-byte verification, and
  stream integrity.
- `test_signing.py` — Verifies signed-URL generation, custom expiry, nonexistent-file
  handling, and URL verification.
- `test_storage.py` — Tests storage backend configuration, checksum computation, storage-path generation, filename sanitization (traversal removal, null-byte removal, length limits), and auto-routing defaults.
- `test_upload.py` — Covers image upload success, invalid file type rejection, and
  media-info retrieval.
