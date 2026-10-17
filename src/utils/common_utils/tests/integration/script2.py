"""
Test program - script2.py

This demonstrates usage of the utilities WITHOUT any setup.
Everything is automatically available because main.py configured them.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Notice: NO SETUP CODE! Just import and use!
import utils.logger as logger
import utils.config as config
import utils.validator as validator


def test_logger():
    """Test logging from a different file - no setup needed!"""
    print("\n" + "=" * 60)
    print("Testing Logger (from script2.py)")
    print("=" * 60)

    logger.info("This is from script2.py - no logger setup needed!")
    logger.debug("Debug: Testing logger from script2")
    logger.warning("Warning: This is a test warning")
    logger.error("Error: This is a test error (not real)")
    logger.critical("Critical: This is a test critical message")

    print("[✓] Logger test complete - check temp/logs/latest.log")


def test_config():
    """Test config reading from a different file - no setup needed!"""
    print("\n" + "=" * 60)
    print("Testing Config (from script2.py)")
    print("=" * 60)

    # Read lots of config keys
    logger.info("Reading configuration values...")

    app_name = config.get("app_name")
    version = config.get("version")
    debug = config.get("debug")
    db_host = config.get("db_host")
    db_port = config.get("db_port")
    max_connections = config.get("max_connections")

    print(f"  App Name: {app_name}")
    print(f"  Version: {version}")
    print(f"  Debug Mode: {debug}")
    print(f"  DB Host: {db_host}")
    print(f"  DB Port: {db_port}")
    print(f"  Max Connections: {max_connections}")

    logger.info(f"Config values: app={app_name}, version={version}")

    # Test setting a new value
    config.set("last_run", "2024-01-15")
    new_val = config.get("last_run")
    print(f"  Set 'last_run' to: {new_val}")
    logger.info(f"Updated config: last_run={new_val}")

    # Test default value
    unknown = config.get("unknown_key", "default_value")
    print(f"  Unknown key with default: {unknown}")

    print("[✓] Config test complete")


def test_validator():
    """Test validation from a different file - no setup needed!"""
    print("\n" + "=" * 60)
    print("Testing Validator (from script2.py)")
    print("=" * 60)

    # Test valid data
    test_cases = [
        ("Hello, world!", True, "Plain text"),
        ("User input: 12345", True, "Alphanumeric"),
        ("SELECT * FROM users", False, "SQL injection attempt"),
        ("DROP TABLE users", False, "SQL DROP attempt"),
        ('"SELECT * FROM users"', True, "Escaped SQL (should pass)"),
        ("<script>alert('xss')</script>", False, "XSS attempt"),
        ("javascript:void(0)", False, "JavaScript injection"),
        ("Normal text with no issues", True, "Safe text"),
        ("DELETE FROM accounts", False, "SQL DELETE attempt"),
        ('"<script>alert(1)</script>"', True, "Escaped XSS (should pass)"),
    ]

    logger.info("Running validation tests...")

    passed = 0
    failed = 0

    for text, expected_valid, description in test_cases:
        result = validator.validate(text)
        success = result.is_valid == expected_valid

        status = "[✓]" if success else "[✗]"
        print(f"  {status} {description}")
        print(f"      Input: {text[:50]}")
        print(f"      Valid: {result.is_valid} (expected: {expected_valid})")

        if result.is_valid:
            logger.debug(f"Validation passed: {description}")
        else:
            logger.warning(f"Validation failed: {description} - {result.error_message}")

        if success:
            passed += 1
        else:
            failed += 1

    print(f"\n  Results: {passed} passed, {failed} failed")
    logger.info(f"Validation tests complete: {passed}/{len(test_cases)} passed")
    print("[✓] Validator test complete")


def test_integration():
    """Test all utilities working together"""
    print("\n" + "=" * 60)
    print("Testing Integration (All utilities together)")
    print("=" * 60)

    logger.info("Starting integration test...")

    # Get app name from config
    app_name = config.get("app_name")
    logger.info(f"Running integration test for: {app_name}")

    # Simulate processing user input
    user_inputs = [
        "Normal user message",
        "SELECT * FROM secrets",
        "Another safe message",
    ]

    for user_input in user_inputs:
        logger.debug(f"Processing input: {user_input}")

        result = validator.validate(user_input)
        if result.is_valid:
            logger.info(f"Input validated: {user_input[:30]}")
            # Would process the input here...
        else:
            logger.error(f"Invalid input blocked: {result.error_message}")

    # Update config based on test
    config.set("last_integration_test", "passed")
    logger.info("Integration test configuration updated")

    print("[✓] Integration test complete")


def run_tests():
    """Run all tests"""
    print("\n")
    print("#" * 60)
    print("# SCRIPT2.PY - Running all tests")
    print("# Notice: NO setup code in this file!")
    print("#" * 60)

    test_logger()
    test_config()
    test_validator()
    test_integration()

    print("\n" + "#" * 60)
    print("# SCRIPT2.PY - All tests complete!")
    print("#" * 60)


if __name__ == "__main__":
    # This should fail because setup wasn't called
    print("ERROR: script2.py should be imported from main.py, not run directly!")
    print("Run main.py instead.")
    sys.exit(1)
