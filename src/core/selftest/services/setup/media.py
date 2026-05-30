"""Media setup mixin.

Creates dummy test media files and resolves log filenames
for selftest endpoint testing.
"""

import time
from pathlib import Path

import src.api as api
import utils.config as config_mod
import utils.logger as logger

from .base import SetupServiceBase


class MediaSetupMixin(SetupServiceBase):
    """Sets up media-related test resources."""

    def create_dummy_test_file(self) -> None:
        try:
            media_dir = Path.home() / ".plexichat" / "media" / "attachments"
            media_dir.mkdir(parents=True, exist_ok=True)
            test_file = media_dir / "test_file.png"
            png_data = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
                b"\x01\x00\x00\x05\x00\x01\r\n\x2e\xe4\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            test_file.write_bytes(png_data)
            logger.debug("Created dummy test file at %s", test_file)

            db_mf = api.get_db()
            if db_mf:
                existing_mf = db_mf.fetch_one(
                    "SELECT id FROM media_files WHERE filename = ? AND deleted = 0",
                    ("test_file.png",),
                )
                if not existing_mf:
                    mf_id = self.ctx.data.generate_snowflake()
                    db_mf.execute(
                        "INSERT INTO media_files (id, filename, original_filename, content_type, size, media_type, storage_backend, storage_path, checksum, uploaded_by, uploaded_at, metadata, scan_status, scan_result, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (
                            mf_id,
                            "test_file.png",
                            "test_file.png",
                            "image/png",
                            len(png_data),
                            "image",
                            "local",
                            str(test_file),
                            "test_checksum_for_selftest",
                            self.ctx.test_user_id,
                            int(time.time()),
                            "{}",
                            "clean",
                            "{}",
                        ),
                    )
                    logger.debug(
                        f"Inserted media_files DB record for test_file.png (id={mf_id})"
                    )
        except Exception as e:
            logger.warning(f"Failed to create dummy test file: {e}")

    def resolve_log_filename(self) -> None:
        try:
            log_dir_raw = config_mod.get("media", {}).get("logs_dir")
            if log_dir_raw:
                log_dir = Path(log_dir_raw).expanduser()
            else:
                log_dir = Path.home() / ".plexichat" / "logs"
            if log_dir.exists():
                log_files = sorted(
                    [
                        f
                        for f in log_dir.iterdir()
                        if f.is_file()
                        and (f.suffix == ".log" or f.name.endswith(".log.zip"))
                    ],
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                if log_files:
                    self.ctx.test_log_filename = log_files[0].name
                    logger.debug(
                        f"Resolved latest log filename: {self.ctx.test_log_filename}"
                    )
        except Exception as e:
            logger.warning(f"Failed to resolve log filename: {e}")
