"""
Test that utilities give helpful errors when setup is not called.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

print("Testing error handling when setup is not called...\n")

# Test 1: Logger without setup
print("[1] Testing logger without setup...")
try:
    import utils.logger as logger

    logger.info("This should fail")
    print("    [FAIL] Should have raised RuntimeError")
except RuntimeError as e:
    print(f"    [PASS] Got expected error: {e}")

# Test 2: Config without setup
print("\n[2] Testing config without setup...")
try:
    import utils.config as config

    config.get("test")
    print("    [FAIL] Should have raised RuntimeError")
except RuntimeError as e:
    print(f"    [PASS] Got expected error: {e}")

# Test 3: Validator without setup (should work - auto-initializes)
print("\n[3] Testing validator without setup (should auto-initialize)...")
try:
    import utils.validator as validator

    result = validator.validate("test")
    print(f"    [PASS] Validator auto-initialized: result.is_valid = {result.is_valid}")
except Exception as e:
    print(f"    [FAIL] Unexpected error: {e}")

print("\n" + "=" * 60)
print("Error handling tests complete!")
print("=" * 60)
