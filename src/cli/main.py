import os
import sys
import secrets
import argparse
from pathlib import Path

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plexichat API Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                 # Start server normally
  python main.py --self-test     # Run API self-test and exit
  python main.py --create-config # Generate default config file
  python main.py --config custom.yaml # Use custom config file
  
  KEK Migration:
  python main.py --migrate-kek --kek-validate --all
  python main.py --migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY
  python main.py --migrate-kek --kek-all --kek-new-env PLEXICHAT_SYSTEM_KEY
  python main.py --migrate-kek --kek-rollback --kek-keyring message_keyring.json
  python main.py --migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY --kek-dry-run
        """,
    )

    parser.add_argument("--config", help="Path to custom configuration file")
    parser.add_argument(
        "--create-config",
        action="store_true",
        help="Create default config file and exit",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="Run automated API self-test and exit"
    )
    parser.add_argument("--host", help="Override server host")
    parser.add_argument("--port", type=int, help="Override server port")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="Rotate rate-limit bypass and webhook signature secrets in config",
    )

    parser.add_argument(
        "--migrate-kek",
        action="store_true",
        help="Run KEK migration tool instead of starting server",
    )
    parser.add_argument(
        "--migrate-db",
        action="store_true",
        help="Run database migrations and exit",
    )
    parser.add_argument(
        "--kek-keyring", help="Specific keyring to migrate (e.g., message_keyring.json)"
    )
    parser.add_argument(
        "--kek-old-env",
        help="Environment variable name for old KEK (e.g., PLEXICHAT_SYSTEM_KEY)",
    )
    parser.add_argument(
        "--kek-new-env",
        help="Environment variable name for new KEK (e.g., PLEXICHAT_MESSAGE_KEY)",
    )
    parser.add_argument(
        "--kek-all",
        action="store_true",
        help="Migrate all keyrings to new KEK (requires --kek-new-env)",
    )
    parser.add_argument(
        "--kek-validate",
        action="store_true",
        help="Validate keyrings without migration",
    )
    parser.add_argument(
        "--kek-rollback", action="store_true", help="Rollback keyring to backup"
    )
    parser.add_argument(
        "--kek-force",
        action="store_true",
        help="Force migration even if validation fails",
    )
    parser.add_argument(
        "--kek-dry-run",
        action="store_true",
        help="Validate only without making changes",
    )

    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    args = parse_args()

    if args.version:
        print(f"Plexichat Server {VERSION}")
        return

    if args.migrate_db:
        handle_migrate_db(project_root)
        return

    if args.migrate_kek:
        handle_migrate_kek(args)
        return

    server = PlexichatServer()

    if args.create_config:
        config_dir = os.path.join(project_root, "config")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "config.yaml")

        if os.path.exists(config_path):
            print(f"Config file already exists at {config_path}")
            return

        with open(config_path, "w") as f:
            yaml.dump(server.get_default_config(), f, default_flow_style=False)
        print(f"Created default configuration at {config_path}")
        return

    early_logs = []

    home_dir = Path.home() / ".plexichat"
    dirs = ["data", "logs", "media", "temp", "config"]
    for d in dirs:
        dir_path = home_dir / d
        dir_path.mkdir(parents=True, exist_ok=True)
        early_logs.append(("debug", f"Ensured directory exists: {dir_path}"))

    config_path = None
    config_source = None

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

    if args.rotate_secrets:
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
            "Successfully rotated rate_limiting.bypass_secret and applications.webhook_signature_secret"
        )
        return

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

    app_config = config.get("application", {})
    logger.info(f"Application: {app_config.get('name', 'Plexichat')}")
    logger.info(f"Version: {VERSION}")
    logger.info(f"Environment: {app_config.get('environment', 'development')}")
    logger.info(f"Config file: {config_path}")

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

    if args.self_test:
        handle_selftest(server, host, port)
        return

    should_restart = server.run(host, port)

    if should_restart:
        logger.info("Restart requested, restarting server...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    logger.info("Server shutdown complete")
