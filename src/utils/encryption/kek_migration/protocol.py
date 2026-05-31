"""Protocol class for KEK Migration mixins."""

import logging
from typing import Optional, Dict, Any
from pathlib import Path


logger = logging.getLogger(__name__)


class KEKMigrationProtocol:
    """Protocol for KEK migration mixins.

    Declares method signatures that are shared across mixin boundaries.
    """

    keyring_path: Path
    dry_run: bool
    backup_path: Path
    temp_path: Path
    rollback_path: Path
    metadata_path: Path

    def validate_keyring(
        self, old_kek: bytes, new_kek: Optional[bytes] = None
    ) -> Dict[str, Any]:
        return super().validate_keyring(old_kek, new_kek)  # type: ignore[misc]

    def backup_keyring(self) -> bool:
        return super().backup_keyring()  # type: ignore[misc]

    def rollback(self) -> bool:
        return super().rollback()  # type: ignore[misc]

    def _save_migration_metadata(
        self, keyring_data: Dict[str, Any], old_kek: bytes, new_kek: bytes
    ) -> None:
        super()._save_migration_metadata(keyring_data, old_kek, new_kek)  # type: ignore[misc]

    def _kek_source_description(self, kek: bytes) -> str:
        return super()._kek_source_description(kek)  # type: ignore[misc]
