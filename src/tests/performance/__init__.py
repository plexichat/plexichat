"""
Performance and load tests for Plexichat critical paths.

Tests cover:
- Authentication (login, registration, token validation)
- Message sending (text, attachments, bulk)
- WebSocket connections (connect, heartbeat, events)
- API endpoints (high concurrency, throughput)
- Memory leaks and resource management
- Performance degradation under load
"""
