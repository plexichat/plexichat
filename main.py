#!/usr/bin/env python3
"""
Plexichat Server - Main Entry Point

This file is the ONLY entry point for the server. All argument parsing,
startup, and subcommand logic actually lives in ``src/cli/main.py``
(the ``src.cli.main`` module); this file simply adds ``src/`` to the import
path and delegates to its ``main()`` function. Keep this file a thin
dispatch wrapper - do not add command logic here.

CLI options (full details in ``python main.py -h`` and
``python main.py <subcommand> -h``):

  Global options (must come before a subcommand):
    --config PATH   Custom YAML config file
                    (default: auto-detect ~/.plexichat/config/config.yaml
                    or ./config/config.yaml)
    --host HOST     Override server bind address (default: 127.0.0.1)
    --port PORT     Override server port (default: 8000)

  Subcommands:
    start           Start the API server (default when no subcommand is given)
    pre-flight      Validate configuration and initialize all modules, then exit
    self-test       Start the server on 127.0.0.1, run the automated API
                    self-test suite against it, then exit
    create-config   Generate a default config file and exit
    version         Show the server version string and exit
    rotate-secrets  Regenerate webhook signature secrets
    migrate-db      Run pending database migrations and exit (--dry-run supported)
    migrate-kek     Manage Key Encryption Key (KEK) migrations
                    (--kek-keyring/--kek-old-env/--kek-new-env/--kek-all/
                    --kek-validate/--kek-rollback/--kek-force/--kek-dry-run)

  Examples:
    python main.py                       Start server normally
    python main.py --config x.yaml       Use custom config (defaults to start)
    python main.py self-test             Run API self-test suite
    python main.py pre-flight            Validate config and modules
    python main.py migrate-db --dry-run  Validate migrations without applying
"""

# pyright: reportAttributeAccessIssue=false
import os
import sys

project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, "src")

for path in [project_root, src_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

from src.cli.main import main  # noqa: E402

if __name__ == "__main__":
    main()
