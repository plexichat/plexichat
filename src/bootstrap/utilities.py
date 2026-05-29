from pathlib import Path

import utils.logger as logger
import utils.config as config
import utils.validator as validator
import utils.version as version

from src.server.lifecycle import VERSION


def setup_utilities() -> None:
    validator.setup()

    versioning_config = config.get("versioning", {})
    version.setup(
        current_version=VERSION,
        min_supported_version=versioning_config.get("min_supported_version", VERSION),
    )

    from src.utils import encryption

    encryption_config = config.get("encryption", {})

    auth_config = config.get("authentication", {})
    enc_security = auth_config.get("encryption", {})
    require_secure = enc_security.get("require_secure_source", True)

    if require_secure:
        from src.utils.encryption.vault import vault

        if not vault.is_using_secure_source():
            error_msg = (
                "CRITICAL SECURITY ERROR: Application is configured to require a secure "
                "encryption key source (TPM, HSM, or Environment Variable), but none was found. "
                "The application has fallen back to an insecure local key file. "
                "To fix: Set PLEXICHAT_SYSTEM_KEY env var or ensure TPM/HSM is accessible. "
                "To bypass (DEV ONLY): Set authentication.encryption.require_secure_source to False."
            )
            logger.critical(error_msg)
            raise RuntimeError(error_msg)

    encryption.setup(
        worker_id=encryption_config.get("snowflake", {}).get("worker_id", 1) or 1,
        datacenter_id=encryption_config.get("snowflake", {}).get("datacenter_id", 1)
        or 1,
        argon2_time_cost=encryption_config.get("argon2", {}).get("time_cost", 2),
        argon2_memory_cost=encryption_config.get("argon2", {}).get(
            "memory_cost", 65536
        ),
        argon2_parallelism=encryption_config.get("argon2", {}).get("parallelism", 2),
    )

    try:
        import utils.licensing as licensing

        licensing.setup()
    except Exception as e:
        logger.error(f"Failed to initialize licensing: {e}")
        raise

    from src.utils.encryption.core import Keyring

    keyring_paths = [
        (Path.home() / ".plexichat" / "data" / "file_keyring.json", None),
        (
            Path.home() / ".plexichat" / "data" / "message_keyring.json",
            "PLEXICHAT_MESSAGE_KEY",
        ),
    ]
    for kpath, kek_env_var in keyring_paths:
        if kpath.exists():
            if kek_env_var:
                kr = Keyring(kpath, kek_env_var)
            else:
                kr = Keyring(kpath)
            if kr.keys:
                logger.info(f"Keyring validated: {kpath.name} (v{kr.current_version})")
