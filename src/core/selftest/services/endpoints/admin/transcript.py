import secrets
import time

import utils.logger as logger

from .base import AdminMixinBase


class TranscriptMixin(AdminMixinBase):
    """Transcript export coverage."""

    def test_transcript_export(self) -> None:
        """Test the full transcript export workflow for a server channel.

        Steps (chaining the created export_id from the POST into the GETs):
            1. Ensure the test channel has a message (send one if needed).
            2. POST a transcript export request -> capture export_id.
            3. GET the export status using the real export_id.
            4. GET the export download using the real export_id.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping transcript export tests (no auth token)")
            return
        if not self.ctx.test_channel_id or not self.ctx.test_server_id:
            logger.debug("Skipping transcript export tests (no test channel)")
            return

        session = self.ctx.session
        channel_id = self.ctx.test_channel_id

        # Step 1: Guarantee the channel has at least one message to export.
        msg_start = time.time()
        msg_resp = session.post(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages",
            json={"content": f"self-test export message {secrets.token_hex(4)}"},
            timeout=10,
        )
        msg_duration = (time.time() - msg_start) * 1000
        msg_ok = 200 <= msg_resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/channels/{channel_id}/messages",
                "status_code": msg_resp.status_code,
                "duration_ms": msg_duration,
                "success": msg_ok,
                "label": "transcript_export_seed_message",
            }
        )
        if not msg_ok:
            logger.warning(
                f"Transcript export seed message -> {msg_resp.status_code}: "
                f"{msg_resp.text[:200]}"
            )
            return

        # Step 2: Request the transcript export.
        logger.info(
            f"Testing POST /api/v1/channels/{channel_id}/messages/export (request)..."
        )
        req_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/export",
            json={"format": "json"},
            timeout=30,
        )
        req_duration = (time.time() - req_start) * 1000
        req_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/channels/{channel_id}/messages/export",
                "status_code": resp.status_code,
                "duration_ms": req_duration,
                "success": req_ok,
                "label": "transcript_export_request",
            }
        )
        if not req_ok:
            logger.warning(
                f"Transcript export request -> {resp.status_code}: {resp.text[:200]}"
            )
            return

        export_id = None
        try:
            data = resp.json()
            export_id = data.get("export_id")
            if export_id:
                self.ctx.test_export_id = str(export_id)
                logger.debug(f"Captured transcript export id: {export_id}")
        except Exception:
            pass

        if not export_id:
            logger.warning("Transcript export response missing export_id")
            return

        # Step 3: Fetch the export status using the real export_id.
        logger.info(
            f"Testing GET /api/v1/channels/{channel_id}/messages/export/"
            f"{export_id} (status)..."
        )
        status_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/export/"
            f"{export_id}",
            timeout=10,
        )
        status_duration = (time.time() - status_start) * 1000
        status_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": f"/api/v1/channels/{channel_id}/messages/export/{export_id}",
                "status_code": resp.status_code,
                "duration_ms": status_duration,
                "success": status_ok,
                "label": "transcript_export_status",
            }
        )
        if status_ok:
            logger.info(f"Transcript export status PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Transcript export status -> {resp.status_code}: {resp.text[:200]}"
            )

        # Step 4: Download the export using the real export_id.
        logger.info(
            f"Testing GET /api/v1/channels/{channel_id}/messages/export/"
            f"{export_id}/download (download)..."
        )
        dl_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/export/"
            f"{export_id}/download",
            timeout=30,
        )
        dl_duration = (time.time() - dl_start) * 1000
        dl_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": (
                    f"/api/v1/channels/{channel_id}/messages/export/"
                    f"{export_id}/download"
                ),
                "status_code": resp.status_code,
                "duration_ms": dl_duration,
                "success": dl_ok,
                "label": "transcript_export_download",
            }
        )
        if dl_ok:
            logger.info(f"Transcript export download PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Transcript export download -> {resp.status_code}: {resp.text[:200]}"
            )
