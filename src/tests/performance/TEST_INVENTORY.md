# Performance Test Inventory

Complete list of all 76 performance tests organized by category.

## Authentication Tests (15 tests)

### test_auth_performance.py::TestAuthPerformance
1. `test_registration_performance` - Benchmark user registration with Argon2 hashing
2. `test_login_performance` - Benchmark login with password verification
3. `test_token_validation_performance` - Benchmark token validation speed
4. `test_concurrent_logins` - Test concurrent login performance (20 users)
5. `test_session_management_performance` - Test session retrieval performance
6. `test_bulk_token_validation` - Test validating 50 tokens in sequence

### test_auth_performance.py::TestAuthMemory
7. `test_registration_memory_leak` - Check for memory leaks during 100 registrations
8. `test_login_memory_leak` - Check for memory leaks during 200 logins
9. `test_token_validation_memory_leak` - Check for memory leaks during 500 validations

### test_auth_performance.py::TestAuthDegradation
10. `test_login_performance_degradation` - Ensure login doesn't degrade over 100 iterations
11. `test_concurrent_registration_scaling` - Test registration scaling with 2/5/10 workers
12. `test_session_table_growth_performance` - Test performance with 50 sessions

## Messaging Tests (14 tests)

### test_messaging_performance.py::TestMessagingPerformance
13. `test_send_message_performance` - Benchmark sending a simple text message
14. `test_get_messages_performance` - Benchmark retrieving 50 messages with pagination
15. `test_bulk_send_performance` - Test sending 100 messages in sequence
16. `test_concurrent_send_performance` - Test 50 concurrent message sends
17. `test_message_search_performance` - Test searching through 200 messages
18. `test_edit_message_performance` - Test message editing performance
19. `test_conversation_list_performance` - Test retrieving 20 conversations

### test_messaging_performance.py::TestMessagingMemory
20. `test_send_message_memory_leak` - Check for memory leaks during 500 sends
21. `test_get_messages_memory_leak` - Check for memory leaks during 200 retrievals
22. `test_large_message_memory` - Test memory usage with 100 large (3000 char) messages

### test_messaging_performance.py::TestMessagingDegradation
23. `test_send_performance_degradation` - Ensure send doesn't degrade over 200 iterations
24. `test_large_conversation_performance` - Test with 1000 message history
25. `test_concurrent_send_scaling` - Test scaling with 1/5/10 workers

## WebSocket Tests (9 tests)

### test_websocket_performance.py::TestWebSocketPerformance
26. `test_connection_creation_performance` - Benchmark WebSocket connection creation
27. `test_event_dispatch_performance` - Benchmark event dispatching to connection
28. `test_concurrent_connections` - Test handling 20 concurrent connections
29. `test_broadcast_performance` - Test broadcasting to 50 connections
30. `test_heartbeat_performance` - Test 100 heartbeat cycles

### test_websocket_performance.py::TestWebSocketMemory
31. `test_connection_memory_leak` - Check for leaks in 50 connection lifecycles
32. `test_event_dispatch_memory_leak` - Check for leaks in 1000 event dispatches

### test_websocket_performance.py::TestWebSocketDegradation
33. `test_connection_count_scaling` - Test scaling with 5/10/20 connections
34. `test_sustained_event_throughput` - Test 1000 events remain stable over 10 batches

## API Tests (12 tests)

### test_api_performance.py::TestAPIEndpointPerformance
35. `test_health_endpoint_performance` - Benchmark health check endpoint
36. `test_register_endpoint_performance` - Benchmark registration endpoint
37. `test_login_endpoint_performance` - Benchmark login endpoint
38. `test_get_messages_endpoint_performance` - Benchmark get messages endpoint
39. `test_send_message_endpoint_performance` - Benchmark send message endpoint
40. `test_concurrent_api_requests` - Test 50 concurrent API requests

### test_api_performance.py::TestAPIThroughput
41. `test_sustained_request_throughput` - Test throughput over 500 requests
42. `test_read_heavy_throughput` - Test read throughput over 200 requests
43. `test_mixed_workload_throughput` - Test mixed read/write over 300 requests

### test_api_performance.py::TestAPIMemory
44. `test_request_handling_memory_leak` - Check for leaks in 500 requests
45. `test_authentication_memory_leak` - Check for leaks in 200 auth requests

### test_api_performance.py::TestAPIDegradation
46. `test_response_time_stability` - Ensure stability over 200 requests
47. `test_concurrent_load_scaling` - Test scaling with different worker counts

## Integration Tests (12 tests)

### test_integration_performance.py::TestCompleteUserJourney
48. `test_complete_registration_to_messaging` - Benchmark complete user flow
49. `test_server_creation_to_messaging` - Benchmark server creation workflow

### test_integration_performance.py::TestServerPerformance
50. `test_large_server_member_operations` - Test operations on server with 10 members
51. `test_permission_check_performance` - Benchmark permission checking
52. `test_server_with_many_channels` - Test performance with 50 channels

### test_integration_performance.py::TestCrossModulePerformance
53. `test_relationship_and_messaging` - Test relationships + messaging integration
54. `test_presence_and_messaging` - Test presence updates during 50 messages
55. `test_notifications_and_messaging` - Test notification generation during 50 messages

### test_integration_performance.py::TestConcurrentWorkflows
56. `test_concurrent_user_registrations_and_messaging` - Test 50 users registering concurrently
57. `test_concurrent_server_operations` - Test 50 concurrent operations on server

### test_integration_performance.py::TestRealWorldScenarios
58. `test_active_conversation_simulation` - Simulate 100 back-and-forth messages
59. `test_server_with_active_channels` - Simulate 100 messages across 5 channels
60. `test_peak_load_simulation` - Simulate 100 concurrent mixed operations

## Stress Tests (14 tests)

### test_stress.py::TestConnectionLimits
61. `test_max_sessions_per_user` - Test behavior with 50 sessions per user
62. `test_many_concurrent_conversations` - Test user with 29 active conversations

### test_stress.py::TestLargeData
63. `test_maximum_message_length` - Test 4000 character message
64. `test_many_messages_in_conversation` - Test conversation with 100+ messages
65. `test_server_with_maximum_members` - Test server with 40 members

### test_stress.py::TestRapidOperations
66. `test_rapid_message_sending` - Test 100 messages in rapid succession
67. `test_rapid_login_logout` - Test 20 login/logout cycles
68. `test_rapid_status_changes` - Test 100 rapid status changes

### test_stress.py::TestConcurrencyStress
69. `test_thundering_herd_login` - Test 50 simultaneous logins
70. `test_concurrent_server_operations_stress` - Test 200 mixed concurrent operations

### test_stress.py::TestRecovery
71. `test_recovery_after_burst` - Test recovery after 500 message burst
72. `test_memory_recovery_after_load` - Test memory recovery after load

### test_stress.py::TestEdgeCases
73. `test_empty_message_handling` - Test edge case: empty message
74. `test_special_characters_message` - Test special characters and emoji
75. `test_rapid_edit_operations` - Test 20 rapid edits of same message
76. `test_zero_pagination_limit` - Test edge case pagination (limit=1)

## Test Distribution

- **Performance benchmarks**: 32 tests (measure speed)
- **Memory leak detection**: 13 tests (check for leaks)
- **Degradation monitoring**: 11 tests (check stability)
- **Concurrency tests**: 15 tests (test parallel execution)
- **Stress tests**: 14 tests (extreme scenarios)
- **Integration tests**: 12 tests (cross-module workflows)

## Coverage Summary

### By Module
- Authentication: 15 tests
- Messaging: 14 tests
- WebSocket: 9 tests
- API: 12 tests
- Integration: 12 tests
- Stress/Edge: 14 tests

### By Type
- Benchmarks (pytest-benchmark): 32 tests
- Memory tracking: 13 tests
- Degradation analysis: 11 tests
- Concurrency: 15 tests
- Integration: 12 tests
- Stress/Edge: 14 tests

### By Duration
- Quick (<30s): 35 tests
- Medium (30s-2min): 28 tests
- Long (>2min): 13 tests

## Running Specific Tests

```bash
# By test class
pytest src/tests/performance/test_auth_performance.py::TestAuthPerformance

# By test name
pytest src/tests/performance/ -k "test_login_performance"

# Multiple tests
pytest src/tests/performance/ -k "test_login or test_registration"

# All memory tests
pytest src/tests/performance/ -k "memory"

# All degradation tests
pytest src/tests/performance/ -k "degradation"

# All concurrent tests
pytest src/tests/performance/ -k "concurrent"
```

## Test Priorities

### P0 (Critical - Run Always)
- test_login_performance
- test_send_message_performance
- test_token_validation_performance
- test_event_dispatch_performance
- test_concurrent_logins

### P1 (Important - Run Frequently)
- All memory leak tests
- All degradation tests
- test_concurrent_send_performance
- test_broadcast_performance
- test_sustained_request_throughput

### P2 (Standard - Run Periodically)
- All integration tests
- Most stress tests
- Scaling tests

### P3 (Edge Cases - Run Occasionally)
- Edge case tests
- Maximum limit tests
- Recovery tests
