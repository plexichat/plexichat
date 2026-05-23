# Integration Tests

This directory contains integration tests that demonstrate the zero-friction usage pattern of the common-utils modules.

## Files

- **main.py**: Demonstrates the setup phase where all utilities are configured once
- **script2.py**: Demonstrates using all utilities without any setup code
- **test_error_handling.py**: Verifies that helpful errors are shown when setup is not called

## Running the Tests

### Full Integration Demo

Run the main integration test that shows the complete workflow:

```bash
python tests\integration\main.py
```

This will:

1. Setup all three utilities (logger, config, validator) in main.py
2. Import and run script2.py which uses all utilities without any setup
3. Create logs in `temp/logs/`
4. Create config in `temp/config.yaml`
5. Run comprehensive validation tests

### Error Handling Test

To verify that utilities provide helpful error messages when setup is not called:

```bash
python tests\integration\test_error_handling.py
```

## What This Demonstrates

The integration tests prove that:

1. **Setup is done once** - Only main.py calls `.setup()` methods
2. **Zero friction usage** - script2.py just imports and uses the modules
3. **No object passing** - No need to pass logger/config/validator objects between functions/files
4. **Helpful errors** - If you forget to call setup, you get a clear error message
5. **Auto-initialization** - Validator auto-initializes with defaults if setup is not called

This is the **ideal** way to use these utilities in a real application!
