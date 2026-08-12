"""Protocol class for KEK Migration mixins."""

import logging
from typing import Optional, Dict, Any, cast
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
        return cast(KEKMigrationProtocol, super()).validate_keyring(old_kek, new_kek)

    def backup_keyring(self) -> bool:
        return cast(KEKMigrationProtocol, super()).backup_keyring()

    def rollback(self) -> bool:
        return cast(KEKMigrationProtocol, super()).rollback()

    def _save_migration_metadata(
        self, keyring_data: Dict[str, Any], old_kek: bytes, new_kek: bytes
    ) -> None:
        cast(KEKMigrationProtocol, super())._save_migration_metadata(
            keyring_data, old_kek, new_kek
        )

    def _kek_source_description(self, kek: bytes) -> str:
        return cast(KEKMigrationProtocol, super())._kek_source_description(kek)
