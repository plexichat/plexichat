# API Self-Test Module

PlexiChat includes an automated self-test module that exercises all registered API endpoints to ensure system stability and reliability.

## Overview

The Self-Test module uses reflection to discover all routes registered in the FastAPI application. It then programmatically makes requests to these endpoints, verifying that they return appropriate status codes and do not trigger internal server errors (500).

## Features

- **Automated Discovery**: Automatically finds all endpoints via the OpenAPI schema.
- **Automated Authentication**: Creates a temporary test admin user to bypass permission checks.
- **Traceback Capture**: Securely captures server-side stack traces for failed requests originating from localhost.
- **Retry Logic**: Automatically retries failed endpoints with elevated debugging.
- **Comprehensive Logging**: Results are logged using the standard application logger. Successful tests are logged at `DEBUG` level, while failures and summaries are logged at `INFO`/`ERROR` levels. Logs are saved to the standard log directory (usually `~/.plexichat/logs/latest.log`).

## Usage

To run the self-test, use the `--self-test` CLI flag:

```bash
python main.py --self-test
```

The server will start listening on `127.0.0.1`, execute the suite, report a summary, and then exit. 

> **Note**: To see all endpoints being tested (including successful ones) in the console, set the logging level to `DEBUG` in your configuration or environment. By default, only failures and the summary are displayed.

## Configuration

The self-test can be configured in `config.yaml` under the `selftest` section:

```yaml
selftest:
  enabled: false
  run_on_startup: false
  exit_on_failure: false
  capture_stack_traces: true
  retry_on_failure: true
  excluded_endpoints: 
    - "/api/v1/auth/logout"
    - "/api/v1/admin/logout"
  test_user:
    username: "selftest_admin"
    email: "selftest@internal.local"
    password: "SelfTest_Password_123!"
```

### Options

| Option | Description |
|--------|-------------|
| `enabled` | Enable/disable the module. |
| `run_on_startup` | If true, runs the test once during normal server startup. |
| `exit_on_failure` | If true, the server will exit if any test fails. |
| `capture_stack_traces` | Allow local requests to capture tracebacks on failure. |
| `excluded_endpoints` | List of endpoints to skip during testing. |

## Security

- **Localhost Only**: Traceback capture is strictly limited to requests originating from `127.0.0.1` or `::1`.
- **Admin Access**: The self-test runner is granted `admin.*` permissions to exercise protected endpoints.
- **Isolated Execution**: When run via CLI, the server only binds to localhost to prevent external access during testing.
