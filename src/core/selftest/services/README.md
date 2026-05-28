# Self-Test Services

Composable service classes that replace the previous mixin hierarchy.
Each service receives a `SelfTestContext` instance and operates on it
explicitly through `self.ctx`, avoiding MRO conflicts.

## Service Catalog

| Service | File | Responsibility |
|---------|------|----------------|
| `DataGenerator` | `data.py` | Snowflake generation, schema-to-body, path-param resolution |
| `RouteDiscovery` | `discovery.py` | OpenAPI spec fetch, route list extraction, feature gating |
| `SetupService` | `setup.py` | Test users, roles, server, channel, resources |
| `CleanupService` | `cleanup.py` | SQL truncation, recursive deletion, API + SQL cleanup |
| `EndpointTester` | `endpoints.py` | Single-endpoint execution, DELETE suite, bot integration |
| `WebSocketTester` | `websocket.py` | Gateway HELLO/IDENTIFY/READY/heartbeat validation |
| `RateLimitTester` | `ratelimit.py` | Burst test for 429 rate-limit enforcement |
| `ReportGenerator` | `report.py` | Pass/fail aggregation and summary |
