# Self-test services

This directory contains composable services for the self-test runner. Each service receives a `SelfTestContext` instance at `plexichat/src/core/selftest/context.py:11` and operates on it through `self.ctx`. This pattern avoids method resolution order (MRO) conflicts that occur with deep mixin hierarchies.

## Services

The runner at `plexichat/src/core/selftest/runner.py:64` composes all services and exposes them on `ctx` so services can call each other.

| Service | Source file | Description |
| --- | --- | --- |
| `DataGenerator` | `plexichat/src/core/selftest/services/data.py:20` | Generate snowflake IDs, build request bodies from OpenAPI schemas, and resolve path parameters to concrete test IDs. |
| `RouteDiscovery` | `plexichat/src/core/selftest/services/discovery.py:15` | Fetch `/openapi.json`, extract the route list, and provide license-gating helpers such as `plexijoin_expected_statuses()`. |
| `SetupService` | `plexichat/src/core/selftest/services/setup/composer.py:19` | Create test users, servers, channels, roles, and feature resources. See [Setup service](setup/README.md). |
| `CleanupService` | `plexichat/src/core/selftest/services/cleanup.py:17` | Perform pre-test truncation, recursive deletion, and API plus SQL cleanup. |
| `EndpointTester` | `plexichat/src/core/selftest/services/endpoints/tester.py:22` | Execute single-endpoint tests, `DELETE` suites, and bot integration. See [Endpoint tester](endpoints/README.md). |
| `WebSocketTester` | `plexichat/src/core/selftest/services/websocket.py` | Validate gateway `HELLO`, `IDENTIFY`, `READY`, and heartbeat flows. |
| `ArtifactsTester` | `plexichat/src/core/selftest/services/artifacts.py` | Run artifact lifecycle, capability, retention, privacy, and federation bridge tests. |
| `SumTester` | `plexichat/src/core/selftest/services/sum.py` | Run the capability-gated PlexiSum real-time fabric test (`WSS SUM_OP` round-trip). |
| `RateLimitTester` | `plexichat/src/core/selftest/services/ratelimit.py` | Send burst traffic to verify that the server returns `429` when rate limits are exceeded. |
| `ReportGenerator` | `plexichat/src/core/selftest/services/report.py` | Aggregate pass, fail, and skip counts and print the summary that `run_all()` returns. |
| `StaticClientTester` | `plexichat/src/core/selftest/services/static_client.py` | Run smoke tests for the static client when the feature is enabled. |
| `DocsTester` | `plexichat/src/core/selftest/services/docs.py` | Run smoke tests for the documentation server. |
| `RamMonitor` | `plexichat/src/core/selftest/services/memory.py` | Monitor memory use during the run and stop cleanly on error paths. |

## Design pattern

Each service follows the same composition rule:

*   Inherit a typed base class that declares `ctx: SelfTestContext` as a class-level attribute. This gives `pyright` a known type for `self.ctx` without `# type: ignore` comments.
*   Accept `ctx` in `__init__` and store it as `self.ctx`.
*   Access shared state only through `self.ctx`, not through cross-service method calls unless explicitly exposed on `ctx`.

Example:

```python
from plexichat.src.core.selftest.context import SelfTestContext
from plexichat.src.core.selftest.services.data import DataGenerator

ctx = SelfTestContext(base_url="http://localhost:8000", config={}, requests_module=requests)
data = DataGenerator(ctx)
snowflake = data.generate_snowflake()
```

## Service lifecycle

The runner initializes services in `SelfTestRunner.__init__` at `plexichat/src/core/selftest/runner.py:54` and stores cross-references on `ctx` at `plexichat/src/core/selftest/runner.py:79`. During `run_all()` at `plexichat/src/core/selftest/runner.py:186`, services execute in this order:

1.  `discovery.discover_routes()`
2.  `cleanup.pre_test_cleanup()`
3.  `setup.run_setup()`
4.  `ws.test_websocket()`, `artifacts.test_artifacts()`, `sum.test_sum()`
5.  `ratelimit.test_rate_limits()`
6.  `endpoints.test_endpoint()` in a loop, including parallel `GET` execution
7.  `cleanup.cleanup_test_data()` and `report.report_summary()`

## Related documentation

*   [Endpoint tester](endpoints/README.md) — mixin breakdown for endpoint execution.
*   [Setup service](setup/README.md) — mixin breakdown for resource creation.
*   `plexichat/src/core/selftest/context.py:11` — complete list of context fields such as `test_server_id`, `test_channel_id`, and `results`.
