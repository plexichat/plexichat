# pyright: reportAttributeAccessIssue=false
"""
Malware-scanning methods mixed into MediaManager.
"""

import logging
from typing import Optional, Tuple

from .models import ScanStatus
from .exceptions import MediaError
from .security import MalwareScanner

logger = logging.getLogger(__name__)


class _ScanningMixin:
    """Malware-scanning methods mixed into MediaManager."""

    def _init_scanner(self) -> MalwareScanner:
        return MalwareScanner(
            host=self._config.get("scanner_host", "localhost"),
            port=self._config.get("scanner_port", 3310),
            enabled=self._config.get("scanner_enabled", False),
        )

    def scan_file(self, file_id: int) -> Tuple[ScanStatus, Optional[str]]:
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")
        if not self._scanner:
            return ScanStatus.SKIPPED, None
        file_data = self._storage.retrieve(file.storage_path)
        status, result = self._scanner.scan_bytes(file_data)
        self._db.execute(
            "UPDATE media_files SET scan_status = ?, scan_result = ? WHERE id = ?",
            (status.value, result, file_id),
        )
        return status, result
