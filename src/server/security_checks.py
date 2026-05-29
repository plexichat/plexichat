import utils.logger as logger
import utils.config as config


def _check_security_keys() -> None:
    warnings = []

    media_config = config.get("media", {})
    signing_key = media_config.get("signing_key", "")
    if signing_key in ["", "CHANGE_THIS_SIGNING_KEY", "change-me", "changeme"]:
        warnings.append("media.signing_key is using a default/placeholder value")

    redis_config = config.get("redis", {})
    if redis_config.get("enabled", False):
        redis_pass = redis_config.get("password", "")
        if not redis_pass:
            warnings.append("redis.password is empty (Redis is enabled)")

    db_config = config.get("database", {})
    if db_config.get("type") == "postgres":
        pg_config = db_config.get("postgres", {})
        pg_pass = pg_config.get("password", "")
        if not pg_pass or pg_pass in ["password", "postgres", "changeme"]:
            warnings.append("database.postgres.password is using a weak/default value")

    voice_config = config.get("voice", {})
    if voice_config.get("enabled", False) and voice_config.get("turn_urls"):
        turn_secret = voice_config.get("turn_secret", "")
        if not turn_secret:
            warnings.append("voice.turn_secret is empty (TURN is configured)")

    messaging_config = config.get("messaging", {})
    if messaging_config.get("encrypt_messages", True):
        try:
            from src.utils.encryption import is_message_key_auto_generated

            if is_message_key_auto_generated():
                warnings.append(
                    "MESSAGE ENCRYPTION: Using auto-generated key. "
                    "Set PLEXICHAT_MESSAGE_KEY env var or back up ~/.plexichat/data/message_keyring.json"
                )
        except Exception:
            warnings.append(
                "messaging.encrypt_messages is enabled (ensure PLEXICHAT_MESSAGE_KEY is set or back up message_keyring.json)"
            )

    if warnings:
        logger.warning("=" * 60)
        logger.warning("SECURITY WARNING: Default/placeholder keys detected!")
        logger.warning("=" * 60)
        for warning in warnings:
            logger.warning(f"  - {warning}")
        logger.warning(
            "Please update these values in your config file for production use."
        )
        logger.warning("=" * 60)
