# Self-test module

Use the self-test module to validate that the Plexichat server is running correctly in the current environment. The module discovers OpenAPI routes and exercises them against the live server.

## Before you begin

Verify the following:

*   The server is running and reachable at the configured base URL.
*   The `requests` library is installed. The runner at `plexichat/src/core/selftest/runner.py:42` raises `RuntimeError` if `requests` is unavailable.

## Architecture

The self-test system is built around a shared context and a set of composable services:

*   `SelfTestContext` at `plexichat/src/core/selftest/context.py:11` holds all mutable state, including auth tokens, resource IDs, results, and service references. Each service receives this context and operates on it through `self.ctx`.
*   `SelfTestRunner` at `plexichat/src/core/selftest/runner.py:42` orchestrates the full lifecycle: route discovery, setup, WebSocket validation, endpoint execution, and cleanup.
*   `ArtifactJournal` at `plexichat/src/core/selftest/artifact_journal.py` tracks privileged artifacts so a subsequent run can reconcile resources left by a crashed run.

The runner executes phases in this order:

1.  Discover routes through `RouteDiscovery` at `plexichat/src/core/selftest/services/discovery.py:15`.
2.  Clean up stale data and create test resources through `CleanupService` and `SetupService`.
3.  Validate the gateway, artifacts, PlexiSum, and rate limiting.
4.  Run the endpoint suite sequentially for mutating methods and in parallel for `GET` requests.
5.  Run standalone flows, deferred destructive tests, bot integration, and `DELETE` coverage.
6.  Clean up test data and generate the report.

For details about supported services, see `plexichat/src/core/selftest/services/README.md`.

## Run the self-test

Run the full suite from the command line:

```bash
python main.py self-test
```

To run the suite without license-gated PlexiJoin tests, pass the `--unlicensed` flag:

```bash
python main.py self-test --unlicensed
```

The account-purge residue check is opt-in and runs on its own:

```bash
python main.py self-test --purge-residue
```

It purges a throwaway account through the admin force-purge endpoint and then
reads the database back, so it never runs as part of the default suite. CI runs
it as its own job (`test:purge_residue`) beside `test:self_test`.

You can also instantiate the runner programmatically:

```python
from plexichat.src.core.selftest import SelfTestRunner

runner = SelfTestRunner(base_url="http://localhost:8000", standalone_mode=True)
success = runner.run_all()
```

The `standalone_mode` parameter controls whether the runner executes destructive endpoints and standalone flows. When you set `standalone_mode=False`, the runner skips endpoints that invalidate sessions or delete shared state. See `plexichat/src/core/selftest/runner.py:45` for the full parameter list.

## Configure the self-test

Configure the suite through `utils.config`:

| Config key | Description |
| --- | --- |
| `selftest.excluded_endpoints` | Route keys to skip, for example `POST:/api/v1/auth/login`. |
| `selftest.enable_admin_tests` | When `False`, excludes all `/admin/` routes. |
| `selftest.parallel_get_workers` | Number of threads for parallel `GET` execution. Default is `8`. |
| `selftest.journal_path` | Path for the persistent artifact journal. |

## Test results

The runner aggregates pass, fail, and skip counts through `ReportGenerator` at `plexichat/src/core/selftest/services/report.py`. `run_all()` returns `True` only when all non-skipped tests pass. Check `ctx.results` for per-endpoint details including status code, latency, and response validation.

## Related documentation

*   [Services](services/README.md) — service catalog and responsibilities.
*   `plexichat/src/core/selftest/context.py` — shared context fields and session management.
*   `plexichat/src/core/selftest/runner.py` — execution order and exclusion logic.
