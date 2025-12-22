"""
Performance test fixtures and configuration.
"""

import pytest
import gc
import tracemalloc
import psutil
import os


@pytest.fixture(scope="function")
def memory_tracker():
    """Track memory usage during test execution."""
    process = psutil.Process(os.getpid())
    
    gc.collect()
    tracemalloc.start()
    
    initial_memory = process.memory_info().rss / 1024 / 1024
    
    class MemoryTracker:
        def __init__(self):
            self.initial_memory = initial_memory
            self.peak_memory = initial_memory
            
        def snapshot(self):
            current_memory = process.memory_info().rss / 1024 / 1024
            self.peak_memory = max(self.peak_memory, current_memory)
            return current_memory
            
        def get_increase(self):
            return self.snapshot() - self.initial_memory
            
        def get_peak_increase(self):
            return self.peak_memory - self.initial_memory
            
        def get_tracemalloc_top(self, limit=10):
            snapshot = tracemalloc.take_snapshot()
            return snapshot.statistics('lineno')[:limit]
    
    tracker = MemoryTracker()
    yield tracker
    
    tracemalloc.stop()
    gc.collect()


@pytest.fixture(scope="function")
def performance_baseline():
    """Provide performance baseline thresholds."""
    return {
        'auth': {
            'register_max_time': 2.0,
            'login_max_time': 1.5,
            'token_validation_max_time': 0.01,
            'concurrent_logins_time': 5.0,
        },
        'messaging': {
            'send_message_max_time': 0.1,
            'get_messages_max_time': 0.2,
            'bulk_send_time': 1.0,
            'concurrent_sends_time': 3.0,
        },
        'websocket': {
            'connect_max_time': 0.5,
            'heartbeat_max_time': 0.01,
            'event_dispatch_max_time': 0.05,
            'concurrent_connections_time': 5.0,
        },
        'api': {
            'endpoint_max_time': 0.5,
            'concurrent_requests_time': 5.0,
            'throughput_min_rps': 100,
        },
        'memory': {
            'max_leak_mb_per_1000_ops': 10,
            'max_total_increase_mb': 100,
        }
    }


@pytest.fixture
def stress_test_users(modules):
    """Create multiple users for stress testing."""
    users = []
    for i in range(50):
        username = f"stresstest_{i}"
        email = f"{username}@example.com"
        password = "StressTest123!@#"
        
        try:
            user = modules.auth.register(
                username=username,
                email=email,
                password=password
            )
            users.append(user)
        except Exception:
            pass
    
    return users


@pytest.fixture
def load_test_server(modules, stress_test_users):
    """Create a server for load testing with many members."""
    if len(stress_test_users) < 10:
        pytest.skip("Not enough users for load test")
    
    owner = stress_test_users[0]
    server = modules.servers.create_server(
        owner_id=owner.id,
        name="Load Test Server"
    )
    
    for user in stress_test_users[1:10]:
        try:
            modules.servers.add_member(server.id, user.id)
        except Exception:
            pass
    
    return server, owner, stress_test_users[1:10]
