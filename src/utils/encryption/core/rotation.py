"""
Key rotation mixin - Automated encryption key rotation.

Part of the EncryptionManager composite class.
"""

import time

import utils.logger as logger
import utils.config as config

from .protocol import EncryptionCoreProtocol


class RotationMixin(EncryptionCoreProtocol):
    """Mixin providing automated key rotation."""

    def rotate_keys(self, force: bool = False) -> bool:
        """Rotate keys if rotation period has passed."""
        rotation_days = config.get("encryption.key_rotation_days", 180)
        rotation_seconds = rotation_days * 24 * 60 * 60

        current_time = int(time.time())
        time_since_rotation = current_time - self.keyring.rotated_at

        if force or time_since_rotation >= rotation_seconds:
            self.keyring.rotate()
            logger.info(
                f"Encryption keys rotated after {time_since_rotation // 86400} days"
            )
            return True

        logger.debug(
            f"Key rotation not needed - {time_since_rotation // 86400} days since last rotation"
        )
        return False
