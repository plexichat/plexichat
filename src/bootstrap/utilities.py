import hashlib
import base64
import os
from pathlib import Path

import utils.logger as logger
import utils.config as config
import utils.validator as validator
import utils.version as version

from src.server.lifecycle import VERSION

_ALLOW_INSECURE_ENV = "PLEXICHAT_ALLOW_INSECURE_SECRETS"


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_pipeline_context() -> bool:
    return (
        os.getenv("CI") is not None
        or os.getenv("GITLAB_CI") is not None
        or bool(config.get("selftest.enabled", False))
        or os.getenv("PLEXICHAT_SELF_TEST") is not None
    )


def _in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as f:
            data = f.read()
        return "docker" in data or "kubepods" in data or "containerd" in data
    except OSError:
        return False


def _is_production_deployment() -> bool:
    if _is_pipeline_context():
        return False
    env_type = (
        str(config.get("application.environment", "development") or "").strip().lower()
    )
    if env_type == "production":
        return True
    if _in_container() and env_type not in {"development", "dev", "test", "testing"}:
        return True
    return False


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
            is_pipeline = _is_pipeline_context()
            production = _is_production_deployment()
            allow_insecure = _truthy(os.getenv(_ALLOW_INSECURE_ENV))
            if is_pipeline:
                logger.warning(
                    "CI/selftest mode detected — bypassing secure source check. "
                    "Set PLEXICHAT_SYSTEM_KEY for production."
                )
            elif production and allow_insecure:
                logger.warning(
                    "INSECURE OVERRIDE ACTIVE: %s is set while require_secure_source is "
                    "enabled in a production deployment — bypassing secure source check. "
                    "This is UNSAFE for real deployments.",
                    _ALLOW_INSECURE_ENV,
                )
            elif not production and not enc_security.get("require_secure_source", True):
                # unreachable given outer if, kept for clarity
                pass
            else:
                # In production, never allow an insecure local fallback.
                # In explicit development (non-production, non-pipeline), still enforce
                # by default unless require_secure_source is disabled in config.
                error_msg = (
                    "CRITICAL SECURITY ERROR: Application requires a secure encryption key "
                    "source (TPM, HSM, or Environment Variable), but none was found. "
                    "An insecure local key fallback is not permitted. "
                    "Fix: set PLEXICHAT_SYSTEM_KEY or configure TPM/HSM. "
                    "DEV ONLY: set authentication.encryption.require_secure_source=false "
                    f"or {_ALLOW_INSECURE_ENV}=true for an explicit temporary override "
                    "(never for real deployments)."
                )
                logger.critical(error_msg)
                raise RuntimeError(error_msg)

    encryption.setup(
        worker_id=encryption_config.get("snowflake", {}).get("worker_id", 1) or 1,
        datacenter_id=encryption_config.get("snowflake", {}).get("datacenter_id", 1)
        or 1,
        argon2_time_cost=encryption_config.get("argon2", {}).get("time_cost", 3),
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

    # Derive PLEXICHAT_MEDIA_KEY from signing_key if not already set
    # so file_keyring.json can be decrypted during early startup
    # (mirrors the same derivation in _storage_setup._init_storage)
    media_config = config.get("media", {})
    signing_key = media_config.get("signing_key")
    if signing_key and signing_key not in [
        "",
        "CHANGE_THIS_SIGNING_KEY",
        "change-me",
        "changeme",
    ]:
        if not os.environ.get("PLEXICHAT_MEDIA_KEY"):
            derived_key = hashlib.sha256(signing_key.encode()).digest()
            os.environ["PLEXICHAT_MEDIA_KEY"] = base64.b64encode(derived_key).decode()

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
