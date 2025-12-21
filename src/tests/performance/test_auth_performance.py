"""
Authentication performance and load tests.

Tests authentication critical paths:
- Registration performance
- Login performance with password hashing
- Token validation speed
- Concurrent login handling
- Session management under load
- Memory leaks in auth operations
"""

import concurrent.futures
from datetime import datetime


class TestAuthPerformance:
    """Test authentication performance."""

    def test_registration_performance(self, benchmark, modules):
        """Benchmark user registration with Argon2 hashing."""
        counter = [0]
        
        def register_user():
            username = f"perfuser_{counter[0]}"
            email = f"{username}@perf.test"
            counter[0] += 1
            
            return modules.auth.register(
                username=username,
                email=email,
                password="SecurePassword123!@#"
            )
        
        result = benchmark(register_user)
        assert result is not None
        assert result.username.startswith("perfuser_")

    def test_login_performance(self, benchmark, modules):
        """Benchmark login with password verification."""
        username = "loginperf_user"
        email = f"{username}@perf.test"
        password = "SecurePassword123!@#"
        
        user = modules.auth.register(username=username, email=email, password=password)
        
        def login():
            return modules.auth.login(username=username, password=password)
        
        result = benchmark(login)
        assert result.token is not None
        assert result.user.id == user.id

    def test_token_validation_performance(self, benchmark, modules):
        """Benchmark token validation speed."""
        username = "tokenperf_user"
        email = f"{username}@perf.test"
        password = "SecurePassword123!@#"
        
        user = modules.auth.register(username=username, email=email, password=password)
        login_result = modules.auth.login(username=username, password=password)
        token = login_result.token
        
        def validate():
            return modules.auth.validate_token(token)
        
        result = benchmark(validate)
        assert result.user_id == user.id

    def test_concurrent_logins(self, benchmark, modules, performance_baseline):
        """Test concurrent login performance."""
        users = []
        for i in range(20):
            username = f"concurrent_{i}"
            email = f"{username}@perf.test"
            password = "SecurePassword123!@#"
            modules.auth.register(username=username, email=email, password=password)
            users.append((username, password))
        
        def concurrent_logins():
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(modules.auth.login, username, password)
                    for username, password in users
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]
            return results
        
        results = benchmark(concurrent_logins)
        assert len(results) == len(users)
        assert all(r.token is not None for r in results)

    def test_session_management_performance(self, benchmark, modules):
        """Test session retrieval performance."""
        username = "sessionperf_user"
        email = f"{username}@perf.test"
        password = "SecurePassword123!@#"
        
        user = modules.auth.register(username=username, email=email, password=password)
        
        for _ in range(10):
            modules.auth.login(username=username, password=password)
        
        def get_sessions():
            return modules.auth.get_sessions(user.id)
        
        sessions = benchmark(get_sessions)
        assert len(sessions) >= 10

    def test_bulk_token_validation(self, benchmark, modules):
        """Test validating many tokens in sequence."""
        tokens = []
        for i in range(50):
            username = f"bulktoken_{i}"
            email = f"{username}@perf.test"
            password = "SecurePassword123!@#"
            modules.auth.register(username=username, email=email, password=password)
            result = modules.auth.login(username=username, password=password)
            tokens.append(result.token)
        
        def validate_all():
            return [modules.auth.validate_token(token) for token in tokens]
        
        results = benchmark(validate_all)
        assert len(results) == 50
        assert all(r.user_id > 0 for r in results)


class TestAuthMemory:
    """Test authentication memory usage and leaks."""

    def test_registration_memory_leak(self, modules, memory_tracker):
        """Check for memory leaks during repeated registrations."""
        initial_memory = memory_tracker.snapshot()
        
        for i in range(100):
            username = f"memleak_{i}"
            email = f"{username}@perf.test"
            password = "SecurePassword123!@#"
            modules.auth.register(username=username, email=email, password=password)
            
            if i % 20 == 0:
                memory_tracker.snapshot()
        
        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory
        
        assert memory_increase < 50, f"Memory increased by {memory_increase}MB, potential leak"

    def test_login_memory_leak(self, modules, memory_tracker):
        """Check for memory leaks during repeated logins."""
        username = "loginmem_user"
        email = f"{username}@perf.test"
        password = "SecurePassword123!@#"
        modules.auth.register(username=username, email=email, password=password)
        
        initial_memory = memory_tracker.snapshot()
        
        for i in range(200):
            modules.auth.login(username=username, password=password)
            
            if i % 40 == 0:
                memory_tracker.snapshot()
        
        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory
        
        assert memory_increase < 30, f"Memory increased by {memory_increase}MB, potential leak"

    def test_token_validation_memory_leak(self, modules, memory_tracker):
        """Check for memory leaks during repeated token validations."""
        username = "tokenmem_user"
        email = f"{username}@perf.test"
        password = "SecurePassword123!@#"
        modules.auth.register(username=username, email=email, password=password)
        result = modules.auth.login(username=username, password=password)
        token = result.token
        
        initial_memory = memory_tracker.snapshot()
        
        for i in range(500):
            modules.auth.validate_token(token)
            
            if i % 100 == 0:
                memory_tracker.snapshot()
        
        final_memory = memory_tracker.snapshot()
        memory_increase = final_memory - initial_memory
        
        assert memory_increase < 10, f"Memory increased by {memory_increase}MB, potential leak"


class TestAuthDegradation:
    """Test authentication performance under sustained load."""

    def test_login_performance_degradation(self, modules):
        """Ensure login performance doesn't degrade over time."""
        username = "degradation_user"
        email = f"{username}@perf.test"
        password = "SecurePassword123!@#"
        modules.auth.register(username=username, email=email, password=password)
        
        times = []
        
        for i in range(100):
            start = datetime.now()
            modules.auth.login(username=username, password=password)
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)
        
        first_batch_avg = sum(times[:10]) / 10
        last_batch_avg = sum(times[-10:]) / 10
        
        degradation = (last_batch_avg - first_batch_avg) / first_batch_avg
        
        assert degradation < 0.5, f"Performance degraded by {degradation*100:.1f}%"

    def test_concurrent_registration_scaling(self, modules):
        """Test how registration scales with concurrency."""
        def register_batch(count, workers):
            users = []
            for i in range(count):
                username = f"scale_{workers}_{i}"
                email = f"{username}@perf.test"
                password = "SecurePassword123!@#"
                users.append((username, email, password))
            
            start = datetime.now()
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(modules.auth.register, u, e, p)
                    for u, e, p in users
                ]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]
            elapsed = (datetime.now() - start).total_seconds()
            
            return elapsed, len(results)
        
        time_2, count_2 = register_batch(10, 2)
        time_5, count_5 = register_batch(10, 5)
        time_10, count_10 = register_batch(10, 10)
        
        assert count_2 == 10
        assert count_5 == 10
        assert count_10 == 10
        
        scaling_efficiency = (time_2 / time_10) / 5
        assert scaling_efficiency > 0.3, f"Poor scaling efficiency: {scaling_efficiency}"

    def test_session_table_growth_performance(self, modules):
        """Test performance impact of large session tables."""
        username = "sessiongrowth_user"
        email = f"{username}@perf.test"
        password = "SecurePassword123!@#"
        user = modules.auth.register(username=username, email=email, password=password)
        
        for _ in range(50):
            modules.auth.login(username=username, password=password)
        
        times = []
        for i in range(20):
            start = datetime.now()
            modules.auth.get_sessions(user.id)
            elapsed = (datetime.now() - start).total_seconds()
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        
        assert avg_time < 0.1, f"Session retrieval too slow with many sessions: {avg_time}s"
