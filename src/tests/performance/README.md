# Performance and Load Tests

Comprehensive performance and load tests for PlexiChat critical paths.

## Overview

These tests measure performance, detect memory leaks, and ensure the system handles load gracefully. They use `pytest-benchmark` for accurate performance measurement and track memory usage to detect leaks.

## Test Structure

### `test_auth_performance.py`
- Registration performance with Argon2 hashing
- Login performance with password verification
- Token validation speed
- Concurrent login handling
- Session management under load
- Memory leaks in auth operations
- Performance degradation over time

### `test_messaging_performance.py`
- Message sending performance
- Message retrieval with pagination
- Bulk message operations
- Concurrent message sending
- Message search performance
- Memory leaks in messaging
- Performance with large conversation history

### `test_websocket_performance.py`
- WebSocket connection establishment
- Heartbeat handling performance
- Event dispatching speed
- Concurrent connection handling
- Broadcast performance
- Memory leaks in connections
- Throughput under sustained load

### `test_api_performance.py`
- API endpoint response times
- Concurrent request handling
- Throughput measurements (req/s)
- Rate limiting impact
- Memory leaks in request handling
- Performance degradation
- Mixed read/write workload

### `test_integration_performance.py`
- Complete user workflows
- Server with many channels/members
- Cross-module interactions
- Real-world usage patterns
- Peak load simulation
- Concurrent workflows

## Running Tests

### All Performance Tests
```bash
pytest src/tests/performance/ -v
```

### Specific Test File
```bash
pytest src/tests/performance/test_auth_performance.py -v
```

### With Benchmark Statistics
```bash
pytest src/tests/performance/ --benchmark-only --benchmark-autosave
```

### With Memory Profiling
```bash
pytest src/tests/performance/ -v -k "memory"
```

## Performance Baselines

The tests include performance baselines defined in `conftest.py`:

- **Authentication**
  - Registration: < 2s (includes Argon2 hashing)
  - Login: < 1.5s
  - Token validation: < 0.01s
  - Concurrent logins (20): < 5s

- **Messaging**
  - Send message: < 0.1s
  - Get messages: < 0.2s
  - Bulk send (100): < 1s
  - Concurrent sends (50): < 3s

- **WebSocket**
  - Connection: < 0.5s
  - Heartbeat: < 0.01s
  - Event dispatch: < 0.05s
  - Concurrent connections (50): < 5s

- **API**
  - Endpoint response: < 0.5s
  - Concurrent requests (50): < 5s
  - Throughput: > 100 req/s (read), > 50 req/s (write)

- **Memory**
  - Max leak per 1000 ops: < 10 MB
  - Max total increase: < 100 MB

## Memory Leak Detection

The `memory_tracker` fixture tracks memory usage during test execution:

```python
def test_memory_leak(modules, memory_tracker):
    initial = memory_tracker.snapshot()
    
    # Perform operations
    for i in range(1000):
        modules.auth.login(username, password)
    
    final = memory_tracker.snapshot()
    increase = final - initial
    
    assert increase < 50, f"Memory increased by {increase}MB"
```

## Degradation Tests

Degradation tests ensure performance remains stable over time:

```python
def test_performance_degradation(modules):
    times = []
    for i in range(100):
        start = time.time()
        modules.auth.login(username, password)
        times.append(time.time() - start)
    
    first_avg = sum(times[:10]) / 10
    last_avg = sum(times[-10:]) / 10
    
    degradation = (last_avg - first_avg) / first_avg
    assert degradation < 0.5  # Max 50% degradation
```

## Concurrency Tests

Tests verify the system handles concurrent operations:

```python
def test_concurrent_operations(modules):
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(modules.auth.login, username, password)
            for _ in range(50)
        ]
        results = [f.result() for f in as_completed(futures)]
    
    assert len(results) == 50
```

## Requirements

Install test dependencies:
```bash
pip install -r requirements-test.txt
```

Required packages:
- `pytest-benchmark` - Performance benchmarking
- `psutil` - Memory tracking
- `httpx` - Async HTTP client for API tests

## Best Practices

1. **Run separately from unit tests** - Performance tests take longer
2. **Use consistent hardware** - Results vary by machine
3. **Minimize background processes** - For accurate measurements
4. **Run multiple times** - Benchmark takes multiple samples
5. **Check memory after GC** - The tracker forces garbage collection
6. **Monitor trends** - Track performance over time

## CI/CD Integration

These tests can be integrated into CI/CD pipelines:

```yaml
# Example GitLab CI
performance-tests:
  script:
    - pytest src/tests/performance/ --benchmark-json=benchmark.json
  artifacts:
    reports:
      junit: benchmark.json
    when: always
```

## Interpreting Results

### Benchmark Output
```
test_login_performance         Mean: 1.234s  StdDev: 0.045s
```
- **Mean**: Average execution time
- **StdDev**: Standard deviation (lower is better)

### Memory Tracking
```
Memory increased by 12.5MB, potential leak
```
- Indicates memory not being freed
- Check for unclosed connections, cached data

### Degradation Warnings
```
Performance degraded by 45.2%
```
- Performance got worse over time
- May indicate cache pollution, resource exhaustion

## Troubleshooting

**Tests are slow**
- Expected! Performance tests do significant work
- Run with `-n auto` for parallel execution where safe

**Memory assertions fail**
- Check if cleanup is happening (e.g., closing connections)
- Verify caches have TTL and max size
- Review object lifecycles

**Benchmark variance is high**
- Ensure consistent test environment
- Close background applications
- Increase benchmark rounds: `--benchmark-min-rounds=20`

**Concurrency tests fail sporadically**
- May indicate race conditions
- Check for proper locking
- Verify thread-safety
