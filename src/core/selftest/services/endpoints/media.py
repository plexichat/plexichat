"""Media endpoint tester mixin.

Tests chunked media upload: create session -> upload chunk -> complete.
"""

import time
import secrets

import utils.logger as logger

from .base import EndpointTesterBase


class MediaMixin(EndpointTesterBase):
    """Tests media upload endpoints."""

    def test_media_upload_complete(self) -> None:
        """Test chunked media upload: create session -> upload chunk -> complete."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_server_id:
            logger.debug("Skipping media upload complete (no server_id)")
            return

        session = self.ctx.session

        # Generate a small random text file in memory
        file_content = secrets.token_hex(64).encode("utf-8")  # 128 bytes of random hex
        filename = f"selftest_{secrets.token_hex(4)}.txt"
        content_type = "text/plain"
        file_size = len(file_content)

        # Step 1: Create upload session
        logger.info("Creating test upload session...")
        create_resp = session.post(
            f"{self.ctx.base_url}/api/v1/media/upload/session",
            json={
                "filename": filename,
                "content_type": content_type,
                "total_size": file_size,
            },
            timeout=5,
        )
        session_id = None
        if create_resp.status_code in (200, 201):
            try:
                session_id = create_resp.json().get("session_id")
            except Exception:
                pass

        if not session_id:
            logger.debug(
                "Skipping media upload complete (could not create upload session)"
            )
            return

        # Step 2: Upload the file as a single chunk
        logger.info(f"Uploading chunk 0 for session {session_id}...")
        chunk_resp = session.post(
            f"{self.ctx.base_url}/api/v1/media/upload/chunk/{session_id}",
            params={"chunk_index": 0},
            files={"file": (filename, file_content, content_type)},
            timeout=15,
        )
        chunk_ok = 200 <= chunk_resp.status_code < 300
        if not chunk_ok:
            logger.warning(
                f"Chunk upload returned {chunk_resp.status_code}: {chunk_resp.text[:200]}"
            )
            # Still try to complete to see what happens

        # Step 3: Complete the upload session
        logger.info(f"Completing upload session {session_id}...")
        complete_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/media/upload/complete/{session_id}",
            timeout=15,
        )
        duration = (time.time() - complete_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/media/upload/complete/{session_id}",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "media_upload_complete",
                "chunk_uploaded": chunk_ok,
                "file_size": file_size,
            }
        )
        if success:
            logger.info(
                f"Media upload complete PASSED -> {resp.status_code} "
                f"(chunk_ok={chunk_ok}, size={file_size}B, {duration:.1f}ms)"
            )
        else:
            logger.warning(
                f"Media upload complete -> {resp.status_code}: {resp.text[:200]}"
            )
