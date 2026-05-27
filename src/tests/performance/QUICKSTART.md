# Performance Tests - Quick Start Guide

## Installation

First, ensure you have the test dependencies installed:

```bash
pip install --require-hashes -r requirements.txt
```

This includes:
- `pytest-benchmark` - Performance benchmarking
- `psutil` - Memory and CPU monitoring
- `httpx` - Async HTTP client for API tests

## Running Tests

### All Performance Tests
```bash
pytest src/tests/performance/ -v
```

### Specific Categories
```bash
# Authentication performance
pytest src/tests/performance/test_auth_performance.py -v

# Messaging performance
pytest src/tests/performance/test_messaging_performance.py -v

# WebSocket performance
pytest src/tests/performance/test_websocket_performance.py -v

# API performance
pytest src/tests/performance/test_api_performance.py -v

# Integration workflows
pytest src/tests/performance/test_integration_performance.py -v

# Stress tests
pytest src/tests/performance/test_stress.py -v
```

### Only Benchmarks (Skip Memory Tests)
```bash
pytest src/tests/performance/ --benchmark-only
```

### Only Memory Tests
```bash
pytest src/tests/performance/ -v -k "memory"
```

### Parallel Execution
```bash
# Use multiple cores (faster but may affect timing accuracy)
pytest src/tests/performance/ -n auto
```

## Interpreting Results

### Benchmark Output
```
test_login_performance
  Mean: 1.234s
  StdDev: 0.045s
  Min: 1.180s
  Max: 1.310s
```

- **Mean**: Average execution time (lower is better)
- **StdDev**: Consistency (lower is more stable)
- **Min/Max**: Best and worst case times

### Memory Output
```
Memory increased by 12.5MB, potential leak
```
- Memory not being freed properly
- Investigate caching, connection pools, object lifecycles

### Pass/Fail Criteria

Tests fail if they exceed thresholds defined in `conftest.py`:
- Authentication: Login < 1.5s, Token validation < 0.01s
- Messaging: Send < 0.1s, Get messages < 0.2s
- WebSocket: Connect < 0.5s, Heartbeat < 0.01s
- API: Response < 0.5s, Throughput > 100 req/s
- Memory: < 10MB leak per 1000 operations

## Common Use Cases

### Pre-Commit Performance Check
```bash
# Quick smoke test
pytest src/tests/performance/ -k "not memory and not stress" --benchmark-disable -v
```

### Full Performance Suite
```bash
# Complete performance validation
pytest src/tests/performance/ -v --benchmark-autosave
```

### Regression Detection
```bash
# Compare against baseline
pytest src/tests/performance/ --benchmark-compare=0001 --benchmark-compare-fail=mean:10%
```

### Continuous Monitoring
```bash
# Save results for trending
pytest src/tests/performance/ --benchmark-autosave --benchmark-save-data
```

## Advanced Options

### Benchmark Options
```bash
# More rounds for accuracy
pytest src/tests/performance/ --benchmark-min-rounds=20

# Calibration warmup
pytest src/tests/performance/ --benchmark-warmup=on

# Statistics output
pytest src/tests/performance/ --benchmark-columns=min,max,mean,stddev,median
```

### Output Formats
```bash
# JSON output
pytest src/tests/performance/ --benchmark-json=results.json

# Histogram
pytest src/tests/performance/ --benchmark-histogram=histogram

# CSV export
pytest src/tests/performance/ --benchmark-save-data --benchmark-save
```

## Test Markers

Filter tests by marker:

```bash
# Only performance tests
pytest -m performance

# Exclude slow tests
pytest -m "not slow"

# Performance but not stress
pytest -m "performance and not stress"
```

## Troubleshooting

### "Not enough users for stress test"
- Stress tests require 50+ test users
- Reduce in `stress_test_users` fixture or skip with `-k "not stress"`

### Tests are very slow
- Expected! Performance tests do significant work
- Use `--benchmark-disable` to skip benchmark measurements
- Run specific test files rather than entire suite

### High variance in results
- Close other applications
- Run multiple times: `--benchmark-min-rounds=10`
- Check system load with `top` or Task Manager

### Memory assertions fail
- Check for resource cleanup (connections, file handles)
- Review cache sizes and TTLs
- Run with GC enabled (default)

### WebSocket tests fail
- Ensure WebSocket module is set up correctly
- Check for port conflicts
- Review connection limits

## CI/CD Integration

Example GitHub Actions workflow:

```yaml
name: Performance Tests

on: [push, pull_request]

jobs:
  performance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install --require-hashes -r requirements.txt
      - name: Run performance tests
        run: pytest src/tests/performance/ --benchmark-json=output.json
      - name: Store benchmark result
        uses: benchmark-action/github-action-benchmark@v1
        with:
          tool: 'pytest'
          output-file-path: output.json
          github-token: ${{ secrets.GITHUB_TOKEN }}
          auto-push: true
```

## Best Practices

1. **Run on consistent hardware** - Results vary by system
2. **Minimize background processes** - For accurate measurements
3. **Run multiple times** - Confirm consistency
4. **Check trends over time** - Use `--benchmark-compare`
5. **Separate quick checks from full suite** - Use markers
6. **Monitor memory throughout** - Not just at end
7. **Test realistic scenarios** - Integration tests matter
8. **Set appropriate thresholds** - Based on requirements

## Quick Reference

| Test Type | Command | Duration |
|-----------|---------|----------|
| Quick smoke test | `pytest src/tests/performance/ -k "not memory and not stress"` | ~2 min |
| Full suite | `pytest src/tests/performance/ -v` | ~10 min |
| Memory only | `pytest src/tests/performance/ -k "memory"` | ~5 min |
| Benchmarks only | `pytest src/tests/performance/ --benchmark-only` | ~8 min |
| Auth only | `pytest src/tests/performance/test_auth_performance.py` | ~2 min |
| Messaging only | `pytest src/tests/performance/test_messaging_performance.py` | ~2 min |

## Getting Help

- See `README.md` for detailed documentation
- Check `conftest.py` for baseline thresholds
- Review `utils.py` for helper functions
- Each test file has inline documentation
