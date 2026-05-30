"""
Test program - main.py

This demonstrates the setup phase where all utilities are configured once.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import utils.logger as logger
import utils.config as config
import utils.validator as validator


def main():
    print("=" * 60)
    print("MAIN.PY - Setting up all utilities")
    print("=" * 60)

    # 1. Setup Logger
    print("\n[1] Setting up logger...")
    logger.setup(
        log_dir="temp/logs",
        level="DEBUG",
        log_name_format="test_app_%Y-%m-%d_%H-%M-%S.log",
    )
    logger.info("Logger initialized successfully!")
    logger.debug("This is a debug message from main.py")

    # 2. Setup Config
    print("[2] Setting up config...")
    defaults = {
        "app_name": "TestApp",
        "version": "1.0.0",
        "debug": True,
        "db_host": "localhost",
        "db_port": 5432,
        "max_connections": 100,
    }
    config.setup(config_path="temp/config.yaml", default_config=defaults)
    logger.info(f"Config initialized! App name: {config.get('app_name')}")

    # 3. Setup Validator (optional - it auto-initializes)
    print("[3] Setting up validator...")
    validator.setup(allow_escaped=True, escape_char='"')
    logger.info("Validator initialized successfully!")

    print("\n[*] All utilities configured!")
    print("[*] Now importing and running script2.py...\n")

    # Import and run the second script
    from script2 import run_tests  # type: ignore[reportMissingImports]

    run_tests()

    print("\n" + "=" * 60)
    print("MAIN.PY - All done!")
    print("=" * 60)


if __name__ == "__main__":
    main()
