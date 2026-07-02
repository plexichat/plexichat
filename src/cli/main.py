"""
Plexichat CLI — Subcommand-based argument parsing and dispatch.

Subcommands:
  start           Start the server (default when no subcommand given)
  pre-flight      Validate configuration, initialize all modules, then exit
  self-test       Start server and run API self-test suite, then exit
  create-config   Generate default config/config.yaml and exit
  version         Show version string and exit
  rotate-secrets  Rotate rate-limit bypass and webhook signature secrets
  migrate-db      Run pending database migrations and exit
  migrate-kek     Enter KEK key migration mode (see subcommand help)
"""

import os
import sys
import re
import secrets
import argparse
from difflib import get_close_matches
from pathlib import Path
from typing import Optional

import yaml

import utils.logger as logger
import utils.config as config
import utils.validator as validator

validator.setup(auto_sanitize_html=True)


from src.server.lifecycle import PlexichatServer, VERSION  # noqa: E402
from src.server.config_loader import setup_config  # noqa: E402
from src.server.security_checks import _check_security_keys  # noqa: E402
from src.server.initializer import initialize_modules  # noqa: E402
from src.bootstrap.logging_setup import setup_logging  # noqa: E402
from src.bootstrap.utilities import setup_utilities  # noqa: E402
from src.bootstrap.app import create_application  # noqa: E402
from src.cli.migrate_db import handle_migrate_db  # noqa: E402
from src.cli.migrate_kek import handle_migrate_kek  # noqa: E402
from src.cli.selftest import handle_selftest  # noqa: E402


project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


_MEDIA_SIGNING_KEY_PLACEHOLDERS = {
    "",
    "CHANGE_THIS_SIGNING_KEY",
    "change-me",
    "changeme",
}


def _ensure_media_signing_key() -> None:
    """Auto-generate ``media.signing_key`` if it is still a placeholder.

    Mirrors the per-installation pattern used for
    ``rate_limiting.bypass_secret`` and ``applications.webhook_signature_secret``
    in ``initializer.initialize_modules``. Runs before
    ``_check_security_keys()`` so the placeholder warning does not fire on
    a fresh install.
    """
    media_config = config.get("media", {})
    current = media_config.get("signing_key", "")
    if current not in _MEDIA_SIGNING_KEY_PLACEHOLDERS:
        return

    media_config["signing_key"] = secrets.token_hex(32)
    config.set("media", media_config)
    logger.info("Auto-generated and persisted media.signing_key")


# ──────────────────────────────────────────────
#  Argument parsing
# ──────────────────────────────────────────────

# List of known subcommands for default-injection and did-you-mean suggestions
_SUBCOMMANDS = frozenset(
    {
        "start",
        "pre-flight",
        "self-test",
        "create-config",
        "version",
        "rotate-secrets",
        "migrate-db",
        "migrate-kek",
    }
)


class _SuggestionParser(argparse.ArgumentParser):
    """ArgumentParser that suggests similar subcommands on invalid choice."""

    def error(self, message: str) -> None:  # type: ignore[override]
        # Suggest close matches when a subcommand is misspelled
        if "invalid choice" in message:
            match = re.search(r"'([^']+)'", message)
            if match:
                bad_cmd = match.group(1)
                suggestions = get_close_matches(bad_cmd, _SUBCOMMANDS, n=3, cutoff=0.4)
                if suggestions:
                    suggestion_str = "' or '".join(suggestions)
                    message += f"\n\nDid you mean '{suggestion_str}'?"
        super().error(message)


def _build_epilog() -> str:
    return """\
Subcommand help:
  python main.py <subcommand> -h     Show help for a specific subcommand

Examples:
  python main.py                     Start server normally
  python main.py --config x.yaml     Use custom config (defaults to start)
  python main.py start -h            Show start subcommand options
  python main.py pre-flight          Validate config and modules
  python main.py self-test           Run API self-test suite
  python main.py create-config       Generate default config
  python main.py version             Show version
  python main.py rotate-secrets      Rotate security secrets
  python main.py migrate-db          Run database migrations
  python main.py migrate-kek -h      Show KEK migration options

KEK Migration:
  python main.py migrate-kek --kek-validate --kek-all
  python main.py migrate-kek --kek-keyring message_keyring.json \\
      --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY
  python main.py migrate-kek --kek-all --kek-new-env PLEXICHAT_SYSTEM_KEY
  python main.py migrate-kek --kek-rollback --kek-keyring message_keyring.json
  python main.py migrate-kek --kek-keyring message_keyring.json \\
      --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY \\
      --kek-dry-run
"""


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """
    Parse command-line arguments.

    Global options (``--config``, ``--host``, ``--port``) must appear
    *before* the subcommand::

        python main.py --config x.yaml start
        python main.py --config x.yaml pre-flight

    When no subcommand is given, ``start`` is injected automatically::

        python main.py --config x.yaml   →  start --config x.yaml
    """
    if argv is None:
        argv = sys.argv[1:]

    # Inject default ``start`` subcommand when no subcommand is present
    argv = _inject_default_subcommand(argv)

    parser = _SuggestionParser(
        description="Plexichat API Server - real-time messaging platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_build_epilog(),
    )
    # Global options (before subcommand) — not inherited by subparsers
    parser.add_argument(
        "--config",
        help="Path to custom YAML configuration file "
        "(default: auto-detect ~/.plexichat/config/config.yaml "
        "or ./config/config.yaml)",
    )
    parser.add_argument(
        "--host",
        help="Override server bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Override server port (default: 8000)",
    )

    # -- Subcommands ----------------------------------------------------
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    def _add(name, **kwargs):
        """Create a subparser."""
        return subparsers.add_parser(name, **kwargs)

    # start
    _add(
        "start",
        help="Start the API server (default)",
        description="Start the Plexichat API server. This is the default "
        "behavior when no subcommand is given.",
    ).set_defaults(handler="_cmd_start")

    # pre-flight
    _add(
        "pre-flight",
        help="Validate configuration and modules, then exit",
        description="Run the full server startup sequence — config loading, "
        "security checks, config validation, utility setup, module "
        "initialization, and application creation — then exit without "
        "binding to a port. Useful for verifying that your configuration "
        "is correct before starting the server.",
    ).set_defaults(handler="_cmd_pre_flight")

    # self-test
    _add(
        "self-test",
        help="Run the API self-test suite and exit",
        description="Start the server on 127.0.0.1, run the automated "
        "self-test suite against it, then shut down and exit. "
        "Returns exit code 0 on success, 1 on failure.",
    ).set_defaults(handler="_cmd_self_test")

    # create-config
    _add(
        "create-config",
        help="Generate a default config file and exit",
        description="Create a default config/config.yaml with all settings "
        "at their factory defaults. If the file already exists, "
        "the command does nothing and exits.",
    ).set_defaults(handler="_cmd_create_config")

    # version
    _add(
        "version",
        help="Show the server version and exit",
        description="Print the current Plexichat server version string and exit.",
    ).set_defaults(handler="_cmd_version")

    # rotate-secrets
    _add(
        "rotate-secrets",
        help="Rotate security secrets in the config file and exit",
        description="Regenerate rate_limiting.bypass_secret and "
        "applications.webhook_signature_secret with new random values "
        "and persist them back to the config file. Requires an existing "
        "config file to modify.",
    ).set_defaults(handler="_cmd_rotate_secrets")

    # migrate-db (with dry-run)
    mdb_parser = _add(
        "migrate-db",
        help="Run pending database migrations and exit",
        description="Connect to the database, apply all pending migrations, "
        "print a summary, and exit. Use --dry-run to validate migrations "
        "without making any changes.",
    )
    mdb_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without making any changes (default: false)",
    )
    mdb_parser.set_defaults(handler="_cmd_migrate_db")

    # migrate-kek  (with sub-options)
    mk_parser = subparsers.add_parser(
        "migrate-kek",
        help="Manage Key Encryption Key (KEK) migrations",
        description="Enter KEK migration mode. Use the sub-options below "
        "to validate, migrate, or rollback keyrings.",
    )
    mk_parser.add_argument(
        "--kek-keyring",
        help="Specific keyring to migrate (e.g., message_keyring.json)",
    )
    mk_parser.add_argument(
        "--kek-old-env",
        help="Environment variable name for the old KEK (e.g., PLEXICHAT_SYSTEM_KEY)",
    )
    mk_parser.add_argument(
        "--kek-new-env",
        help="Environment variable name for the new KEK (e.g., PLEXICHAT_MESSAGE_KEY)",
    )
    mk_parser.add_argument(
        "--kek-all",
        action="store_true",
        help="Migrate all keyrings to the new KEK (requires --kek-new-env)",
    )
    mk_parser.add_argument(
        "--kek-validate",
        action="store_true",
        help="Validate keyring integrity without making changes",
    )
    mk_parser.add_argument(
        "--kek-rollback",
        action="store_true",
        help="Rollback a keyring from its backup file",
    )
    mk_parser.add_argument(
        "--kek-force",
        action="store_true",
        help="Force migration even if validation checks fail",
    )
    mk_parser.add_argument(
        "--kek-dry-run",
        action="store_true",
        help="Simulate migration without writing any changes",
    )
    mk_parser.set_defaults(handler="_cmd_migrate_kek")

    args = parser.parse_args(argv)

    # Default to 'start' when no subcommand is given
    if args.command is None:
        args.command = "start"
        args.handler = "_cmd_start"

    return args


def _inject_default_subcommand(argv: list) -> list:
    """
    If *argv* does not contain any recognised subcommand as its first
    non-option token, append ``"start"`` so that global options (currently
    defined only on subparsers) are still accepted.

    Examples::

        []                     →  ["start"]
        ["--config", "x.yaml"] →  ["--config", "x.yaml", "start"]
        ["start", "--config"]  →  ["start", "--config"]   (unchanged)
        ["pre-flight"]         →  ["pre-flight"]          (unchanged)
    """
    # If any token is a recognised subcommand, leave argv as-is.
    for token in argv:
        if token in _SUBCOMMANDS:
            return argv

    # No subcommand found — inject ``start`` at the end.
    return argv + ["start"]


# ──────────────────────────────────────────────
#  Shared startup sequence
# ──────────────────────────────────────────────


def _run_startup(args: argparse.Namespace, project_root: str) -> tuple:
    """
    Run the common server startup sequence.

    1. Create home directories
    2. Load configuration
    3. Setup logging
    4. Run security key checks
    5. Validate config type structure
    6. Setup utilities (validator, encryption, licensing, keyrings)
    7. Initialize all core modules
    8. Create the FastAPI application

    Returns:
        (server, host, port, config_path)
    """
    server = PlexichatServer()

    early_logs = []

    home_dir = Path.home() / ".plexichat"
    dirs = ["data", "logs", "media", "temp", "config"]
    for d in dirs:
        dir_path = home_dir / d
        dir_path.mkdir(parents=True, exist_ok=True)
        early_logs.append(("debug", f"Ensured directory exists: {dir_path}"))

    config_path: Optional[str] = None
    config_source: Optional[str] = None

    if args.config:
        config_path = args.config
        config_source = "command line argument"
    elif os.environ.get("PLEXICHAT_CONFIG"):
        config_path = os.environ.get("PLEXICHAT_CONFIG")
        config_source = "PLEXICHAT_CONFIG environment variable"

    if config_path:
        config.setup(
            config_path=config_path, default_config=server.get_default_config()
        )
        early_logs.append(
            ("info", f"Config loaded from {config_source}: {config_path}")
        )
    else:
        config_path = setup_config(project_root, server.get_default_config())
        early_logs.append(
            ("info", f"Config loaded from auto-detected path: {config_path}")
        )

    log_config = config.get("logging", {})
    media_config = config.get("media", {})
    log_dir = media_config.get("logs_dir", os.path.join(project_root, "logs"))
    log_dir = os.path.expanduser(log_dir)

    early_logs.append(("info", "Initializing logger..."))
    early_logs.append(("info", f"  Log directory: {log_dir}"))
    early_logs.append(("info", f"  Log level: {log_config.get('level', 'INFO')}"))

    setup_logging(project_root)

    for level, msg in early_logs:
        getattr(logger, level)(msg)

    logger.info("=" * 60)
    logger.info("Plexichat Server Starting")
    logger.info("=" * 60)

    app_cfg = config.get("application", {})
    logger.info(f"Application: {app_cfg.get('name', 'Plexichat')}")
    logger.info(f"Version: {VERSION}")
    logger.info(f"Environment: {app_cfg.get('environment', 'development')}")
    logger.info(f"Config file: {config_path}")

    _ensure_media_signing_key()
    _check_security_keys()

    try:
        server.validate_config()
        logger.info("Configuration validated successfully")
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        if os.getenv("NO_STRICT_CONFIG") != "true":
            sys.exit(1)

    setup_utilities()

    db, main_modules = initialize_modules(server._modules, server.worker_id)
    server.db = db

    app = create_application(server, *main_modules)
    server.app = app

    host = args.host or os.getenv(
        "HOST", config.get("server", {}).get("host", "127.0.0.1")
    )
    port = args.port or int(
        os.getenv("PORT", config.get("server", {}).get("port", 8000))
    )

    return server, host, port, config_path


# ──────────────────────────────────────────────
#  Subcommand handlers
# ──────────────────────────────────────────────


def _cmd_version(args: argparse.Namespace) -> None:
    print(f"Plexichat Server {VERSION}")


def _cmd_create_config(args: argparse.Namespace) -> None:
    """Generate a default ``config.yaml``.

    Writes to the first writable location in Plexichat's config search order:
    ``~/.plexichat/config/config.yaml`` (preferred) and falls back to
    ``<project_root>/config/config.yaml`` only if the home directory is
    unavailable. If a config file already exists at the chosen location,
    the command is a no-op and reports the existing path.
    """
    candidates = [
        (str(Path.home() / ".plexichat" / "config"), "home directory"),
        (os.path.join(project_root, "config"), "project directory"),
    ]

    target_dir: Optional[str] = None
    target_source: Optional[str] = None
    for candidate_dir, source in candidates:
        try:
            os.makedirs(candidate_dir, exist_ok=True)
            target_dir = candidate_dir
            target_source = source
            break
        except (OSError, PermissionError) as exc:
            print(f"Cannot use {source} ({candidate_dir}): {exc}")
            continue

    if target_dir is None:
        print("No writable config directory found. Aborting.")
        return

    config_path = os.path.join(target_dir, "config.yaml")
    if os.path.exists(config_path):
        print(f"Config file already exists at {config_path}")
        return

    server = PlexichatServer()
    with open(config_path, "w") as f:
        yaml.dump(server.get_default_config(), f, default_flow_style=False)
    print(f"Created default configuration at {config_path} ({target_source})")


def _cmd_rotate_secrets(args: argparse.Namespace) -> None:
    # Bootstrap enough config to locate and load the file
    server = PlexichatServer()
    config_path: Optional[str] = None

    if args.config:
        config_path = args.config
    elif os.environ.get("PLEXICHAT_CONFIG"):
        config_path = os.environ.get("PLEXICHAT_CONFIG")

    if config_path:
        config.setup(
            config_path=config_path, default_config=server.get_default_config()
        )
    else:
        config_path = setup_config(project_root, server.get_default_config())

    print(f"Rotating secrets in {config_path}...")
    rl_config = config.get("rate_limiting", {})
    app_config = config.get("applications", {})

    rl_config["bypass_secret"] = secrets.token_hex(32)
    app_config["webhook_signature_secret"] = secrets.token_hex(32)

    config.set("rate_limiting", rl_config)
    config.set("applications", app_config)

    with open(config_path, "w") as f:
        yaml.dump(config.get_all(), f, default_flow_style=False)

    print(
        "Successfully rotated rate_limiting.bypass_secret "
        "and applications.webhook_signature_secret"
    )


def _cmd_migrate_db(args: argparse.Namespace) -> None:
    handle_migrate_db(project_root, dry_run=getattr(args, "dry_run", False))


def _cmd_migrate_kek(args: argparse.Namespace) -> None:
    handle_migrate_kek(args)


def _cmd_self_test(args: argparse.Namespace) -> None:
    server, host, port, _config_path = _run_startup(args, project_root)
    handle_selftest(server, host, port)


def _cmd_pre_flight(args: argparse.Namespace) -> None:
    """Run full startup then exit without binding to a port."""
    _run_startup(args, project_root)
    logger.info("=" * 60)
    logger.info("Pre-flight checks passed: configuration is valid.")
    logger.info("=" * 60)
    logger.info("All modules initialized successfully. No port was bound.")
    logger.info("The server is ready to start. Run 'python main.py start' to serve.")


def _cmd_start(args: argparse.Namespace) -> None:
    """Full startup and serve."""
    server, host, port, _config_path = _run_startup(args, project_root)

    should_restart = server.run(host, port)

    if should_restart:
        logger.info("Restart requested, restarting server...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    logger.info("Server shutdown complete")


# ──────────────────────────────────────────────
#  Dispatch table
# ──────────────────────────────────────────────

_HANDLERS = {
    "_cmd_version": _cmd_version,
    "_cmd_create_config": _cmd_create_config,
    "_cmd_rotate_secrets": _cmd_rotate_secrets,
    "_cmd_migrate_db": _cmd_migrate_db,
    "_cmd_migrate_kek": _cmd_migrate_kek,
    "_cmd_pre_flight": _cmd_pre_flight,
    "_cmd_self_test": _cmd_self_test,
    "_cmd_start": _cmd_start,
}


def main(argv: Optional[list] = None) -> None:
    """
    Top-level CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).
    """
    args = parse_args(argv)
    handler = _HANDLERS.get(args.handler)
    if handler is None:
        print(f"Error: unknown handler '{args.handler}'", file=sys.stderr)
        sys.exit(1)
    handler(args)
