"""Utilities must fail with a helpful error when setup() was never called.

This is the check the integration README has always described as
``test_error_handling.py``. It used to live in ``error_handling_demo.py`` as a
module-level script: the checks, their ``print`` calls and a ``sys.path``
insertion all ran on *import*, so anything importing the module -- a test
sweep, a documentation build, an IDE index -- executed the demo as a side
effect, and the reported result depended on whether the importer had already
configured the utilities (it printed a false ``[FAIL]`` whenever it had).

The checks are now real tests, and the demo output is confined to ``main()``,
which only runs when the file is executed directly:

    python src/utils/common_utils/tests/integration/test_error_handling.py

``_setup_called`` is forced off per test rather than assumed: another suite
sharing the worker may have configured the utilities already, and the
``reset_singletons`` fixture restores whatever state the test found.
"""

import os
import sys

if __name__ == "__main__":
    # Only a direct run needs the package root on the path (the same bootstrap
    # ``main.py`` uses); importing this module must not mutate sys.path.
    sys.path.insert(
        0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    )

import pytest  # noqa: E402

import utils.config as config  # noqa: E402
import utils.logger as logger  # noqa: E402
import utils.validator as validator  # noqa: E402


@pytest.mark.integration
def test_logger_reports_helpful_error_before_setup(monkeypatch):
    monkeypatch.setattr(logger, "_setup_called", False)
    with pytest.raises(RuntimeError, match="Logger not configured"):
        logger.info("This should fail")


@pytest.mark.integration
def test_config_reports_helpful_error_before_setup(monkeypatch):
    monkeypatch.setattr(config, "_setup_called", False)
    with pytest.raises(RuntimeError, match="Config not configured"):
        config.get("app_name")


@pytest.mark.integration
def test_validator_needs_no_setup():
    """The validator auto-initializes with defaults, unlike config/logger."""
    result = validator.validate("plain text")

    assert result.is_valid
    assert result.error_message is None


def main() -> None:
    print("Testing error handling when setup is not called...\n")

    # Test 1: Logger without setup
    print("[1] Testing logger without setup...")
    try:
        logger.info("This should fail")
        print("    [FAIL] Should have raised RuntimeError")
    except RuntimeError as exc:
        print(f"    [PASS] Got expected error: {exc}")

    # Test 2: Config without setup
    print("\n[2] Testing config without setup...")
    try:
        config.get("test")
        print("    [FAIL] Should have raised RuntimeError")
    except RuntimeError as exc:
        print(f"    [PASS] Got expected error: {exc}")

    # Test 3: Validator without setup (should work - auto-initializes)
    print("\n[3] Testing validator without setup (should auto-initialize)...")
    try:
        result = validator.validate("test")
        print(
            f"    [PASS] Validator auto-initialized: result.is_valid = {result.is_valid}"
        )
    except Exception as exc:  # noqa: BLE001 - demo reports any failure
        print(f"    [FAIL] Unexpected error: {exc}")

    print("\n" + "=" * 60)
    print("Error handling tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
