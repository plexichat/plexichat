"""Boot-time security validation.

Validates security-critical configuration and aborts startup when a real
(non-CI/selftest) production deployment would run with insecure defaults.
Dev/pipeline contexts may proceed with warnings; explicit dev opt-outs in
production-like environments are honored but logged loudly.

No secret values are ever written to logs or error messages — only key
names / categories.
"""

import os

import utils.config as config
import utils.logger as logger

# Placeholder/weak values that must never be used in production.
_SIGNING_KEY_PLACEHOLDERS = frozenset(
    {"", "CHANGE_THIS_SIGNING_KEY", "change-me", "changeme"}
)
_WEAK_DB_PASSWORDS = frozenset({"", "password", "postgres", "changeme"})
# Env/config values that are obviously not real secrets (docs/examples).
_SECRET_PLACEHOLDER_MARKERS = (
    "auto-generated-keep-secure",
    "replace-with-",
    "your-secure-password",
    "your-redis-password",
    "your-restic-repo-password",
    "your-smtp-password",
    "change_this",
    "changeme",
    "example",
)

_ALLOW_INSECURE_ENV = "PLEXICHAT_ALLOW_INSECURE_SECRETS"


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def is_pipeline_context() -> bool:
    """True for CI, GitLab CI, and explicit self-test modes."""
    return (
        os.getenv("CI") is not None
        or os.getenv("GITLAB_CI") is not None
        or bool(config.get("selftest.enabled", False))
        or os.getenv("PLEXICHAT_SELF_TEST") is not None
    )


def _in_container() -> bool:
    """Best-effort container detection (Docker/Kubernetes)."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as f:
            data = f.read()
        if "docker" in data or "kubepods" in data or "containerd" in data:
            return True
    except OSError:
        pass
    return False


def _allow_insecure_opt_out() -> bool:
    return _truthy(os.getenv(_ALLOW_INSECURE_ENV))


def is_production_deployment() -> bool:
    """True when this boot should be treated as a real production deployment.

    Explicit ``application.environment=production`` always qualifies.
    Containerized boots are treated as production deployments unless an
    explicit, loud dev opt-out is active (they should set
    ``application.environment`` appropriately via config/profile).
    CI/selftest is never treated as production here.
    """
    if is_pipeline_context():
        return False
    env_type = (
        str(config.get("application.environment", "development") or "").strip().lower()
    )
    if env_type == "production":
        return True
    # Docker/compose default profile is a real deployment profile; docker-config
    # sets environment=production. As defense-in-depth, treat container boots as
    # production-like unless a dev environment is explicitly selected.
    if _in_container() and env_type not in {"development", "dev", "test", "testing"}:
        return True
    return False


def _looks_like_placeholder_secret(value: str | None) -> bool:
    if value is None:
        return True
    v = str(value).strip()
    if not v:
        return True
    low = v.lower()
    return any(marker in low for marker in _SECRET_PLACEHOLDER_MARKERS)


def _check_security_keys() -> None:
    warnings: list[str] = []

    media_config = config.get("media", {}) or {}
    signing_key = str(media_config.get("signing_key", "") or "")
    if signing_key in _SIGNING_KEY_PLACEHOLDERS:
        warnings.append("media.signing_key is using a default/placeholder value")

    valkey_config = config.get("valkey", {}) or {}
    if valkey_config.get("enabled", False):
        valkey_pass = str(valkey_config.get("password", "") or "")
        if not valkey_pass:
            warnings.append("valkey.password is empty (Valkey is enabled)")
        elif _looks_like_placeholder_secret(valkey_pass):
            warnings.append("valkey.password appears to be a placeholder value")

    db_config = config.get("database", {}) or {}
    if db_config.get("type") == "postgres":
        pg_config = db_config.get("postgres", {}) or {}
        pg_pass = str(pg_config.get("password", "") or "")
        if pg_pass in _WEAK_DB_PASSWORDS:
            warnings.append("database.postgres.password is using a weak/default value")
        elif _looks_like_placeholder_secret(pg_pass):
            warnings.append(
                "database.postgres.password appears to be a placeholder value"
            )

    voice_config = config.get("voice", {}) or {}
    if voice_config.get("enabled", False) and voice_config.get("turn_urls"):
        turn_secret = str(voice_config.get("turn_secret", "") or "")
        if not turn_secret:
            warnings.append("voice.turn_secret is empty (TURN is configured)")

    messaging_config = config.get("messaging", {}) or {}
    if messaging_config.get("encrypt_messages", True):
        try:
            from src.utils.encryption import is_message_key_auto_generated

            if is_message_key_auto_generated():
                warnings.append(
                    "message encryption key is auto-generated (set PLEXICHAT_MESSAGE_KEY "
                    "or back up the message keyring for production)"
                )
        except Exception:
            warnings.append(
                "messaging.encrypt_messages is enabled (ensure PLEXICHAT_MESSAGE_KEY "
                "is configured or message keyring is backed up)"
            )

    # Critical key env vars: flag obvious placeholders only (never log values).
    for env_name in (
        "PLEXICHAT_SYSTEM_KEY",
        "PLEXICHAT_MESSAGE_KEY",
        "PLEXICHAT_MEDIA_KEY",
    ):
        raw = os.getenv(env_name)
        if raw is not None and raw != "" and _looks_like_placeholder_secret(raw):
            warnings.append(
                f"{env_name} environment variable appears to be a placeholder value"
            )

    if not warnings:
        return

    production = is_production_deployment()
    opt_out = _allow_insecure_opt_out()

    logger.warning("=" * 60)
    if production:
        logger.warning(
            "SECURITY: Insecure/placeholder secrets detected in a production deployment!"
        )
    else:
        logger.warning("SECURITY WARNING: Insecure/placeholder secrets detected!")
    logger.warning("=" * 60)
    for warning in warnings:
        logger.warning("  - %s", warning)
    if production and opt_out:
        logger.warning(
            "INSECURE OVERRIDE ACTIVE: %s is set. This is UNSAFE for real deployments. "
            "Fix the issues above; do not rely on this bypass.",
            _ALLOW_INSECURE_ENV,
        )
    logger.warning("Update configuration/secrets before production use.")
    logger.warning("=" * 60)

    if production and not opt_out:
        raise ValueError(
            "Production boot aborted due to insecure security configuration "
            f"({len(warnings)} issue(s)). Set {_ALLOW_INSECURE_ENV}=true only for "
            "explicit, temporary development overrides (NOT for real deployments)."
        )


# Backwards-compatible alias for older callers/imports.
check_security_keys = _check_security_keys
