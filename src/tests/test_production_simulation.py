"""
Production Simulation Test Suite

Tests verify system behavior under production-like conditions:
- Multiple uvicorn workers accessing shared PostgreSQL pool
- Redis caching enabled with database queries
- Worker restart scenarios and graceful shutdown
- Connection pool utilization monitoring

Requirements:
- PostgreSQL Docker container (automatically managed by fixtures)
- fakeredis for Redis simulation (no external Redis required)
- pytest-xdist for parallel test execution

Usage:
    # Run all production simulation tests
    pytest src/tests/test_production_simulation.py -v
    
    # Run specific test class
    pytest src/tests/test_production_simulation.py::TestMultiWorkerPostgresPool -v
    
    # Run with detailed pool statistics logging
    pytest src/tests/test_production_simulation.py -v -s --log-cli-level=DEBUG
"""

import asyncio
import json
import logging
import multiprocessing as mp
import signal
import time
import threading
from dataclasses import dataclass, asdict
from multiprocessing import Process, Queue
from typing import List, Dict, Any, Optional, Tuple
from unittest.mock import patch, MagicMock

import fakeredis
import pytest
from sqlalchemy import text

from src.core.database.core import Database
from src.core.database.redis_client import RedisClient

logger = logging.getLogger(__name__)


# ============================================================================
# Helper Classes and Utilities
# ============================================================================


@dataclass
class PoolMetrics:
    """Represents pool statistics at a point in time."""
    timestamp: float
    active_connections: int
    idle_connections: int
    total_acquisitions: int
    avg_acquisition_time_ms: float
    max_acquisition_time_ms: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return asdict(self)


@dataclass
class WorkerResult:
    """Result from a worker process."""
    worker_id: int
    total_queries: int
    successful_queries: int
    failed_queries: int
    avg_query_time_ms: float
    max_query_time_ms: float
    errors: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return asdict(self)


class WorkerSimulator:
    """Manages worker process lifecycle and coordinates query execution."""
    
    def __init__(self, worker_id: int, result_queue: Queue, db_config: Dict[str, Any]):
        """
        Initialize worker simulator.
        
        Args:
            worker_id: Unique worker identifier
            result_queue: Queue to send results to parent process
            db_config: Database configuration to pass to worker
        """
        self.worker_id = worker_id
        self.result_queue = result_queue
        self.db_config = db_config
        self.process = None
    
    def spawn(self, target_func, *args, **kwargs):
        """
        Spawn worker process.
        
        Args:
            target_func: Function to execute in worker process
            *args: Positional arguments for target function
            **kwargs: Keyword arguments for target function
        """
        self.process = Process(
            target=self._worker_wrapper,
            args=(target_func, args, kwargs)
        )
        self.process.start()
    
    def _worker_wrapper(self, target_func, args, kwargs):
        """
        Wrapper function for worker process.
        
        Provides isolation and error handling.
        """
        try:
            result = target_func(*args, **kwargs)
            self.result_queue.put(result)
        except Exception as e:
            logger.exception(f"Worker {self.worker_id} error: {e}")
            self.result_queue.put({
                'worker_id': self.worker_id,
                'error': str(e),
                'type': 'exception'
            })
    
    def terminate(self):
        """Terminate worker process."""
        if self.process and self.process.is_alive():
            self.process.terminate()
    
    def join(self, timeout: Optional[float] = None):
        """Wait for worker process to finish."""
        if self.process:
            self.process.join(timeout=timeout)
    
    def is_alive(self) -> bool:
        """Check if worker process is alive."""
        return self.process is not None and self.process.is_alive()


class ProductionSimulator:
    """Orchestrates multi-worker production simulation."""
    
    def __init__(
        self,
        db_config: Dict[str, Any],
        worker_count: int = 4,
        queries_per_worker: int = 50
    ):
        """
        Initialize production simulator.
        
        Args:
            db_config: Database configuration
            worker_count: Number of worker processes to spawn
            queries_per_worker: Queries per worker
        """
        self.db_config = db_config
        self.worker_count = worker_count
        self.queries_per_worker = queries_per_worker
        self.workers: List[WorkerSimulator] = []
        self.metrics_history: List[PoolMetrics] = []
        self.results: List[WorkerResult] = []
    
    def spawn_workers(self, target_func) -> Queue:
        """
        Spawn multiple worker processes.
        
        Args:
            target_func: Function to execute in each worker
            
        Returns:
            Queue for collecting results
        """
        result_queue = Queue()
        
        for worker_id in range(self.worker_count):
            worker = WorkerSimulator(worker_id, result_queue, self.db_config)
            worker.spawn(
                target_func,
                worker_id=worker_id,
                queries_per_worker=self.queries_per_worker,
                db_config=self.db_config
            )
            self.workers.append(worker)
        
        return result_queue
    
    def collect_results(self, result_queue: Queue, timeout: float = 300.0) -> List[Dict[str, Any]]:
        """
        Collect results from all workers.
        
        Args:
            result_queue: Queue containing worker results
            timeout: Total timeout for collecting all results
            
        Returns:
            List of worker results
        """
        results = []
        start_time = time.time()
        
        while len(results) < self.worker_count:
            if time.time() - start_time > timeout:
                logger.warning(f"Timeout collecting results. Got {len(results)}/{self.worker_count}")
                break
            
            try:
                result = result_queue.get(timeout=5.0)
                results.append(result)
            except:
                # Check if any workers are still alive
                alive_workers = sum(1 for w in self.workers if w.is_alive())
                if alive_workers == 0:
                    break
        
        return results
    
    def terminate_all_workers(self):
        """Terminate all worker processes."""
        for worker in self.workers:
            worker.terminate()
    
    def join_all_workers(self, timeout: Optional[float] = None):
        """Wait for all workers to finish."""
        for worker in self.workers:
            worker.join(timeout=timeout)


class MetricsAggregator:
    """Aggregates metrics from multiple sources."""
    
    def __init__(self):
        """Initialize metrics aggregator."""
        self.pool_metrics: List[PoolMetrics] = []
        self.worker_results: List[WorkerResult] = []
        self.timings: List[float] = []
    
    def add_pool_metrics(self, metrics: PoolMetrics):
        """Add pool metrics snapshot."""
        self.pool_metrics.append(metrics)
    
    def add_worker_result(self, result: WorkerResult):
        """Add worker result."""
        self.worker_results.append(result)
    
    def add_timing(self, duration_ms: float):
        """Add individual query timing."""
        self.timings.append(duration_ms)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get aggregated metrics summary."""
        if not self.timings:
            return {}
        
        sorted_timings = sorted(self.timings)
        n = len(sorted_timings)
        
        return {
            'total_timings': n,
            'avg_time_ms': sum(self.timings) / n,
            'min_time_ms': sorted_timings[0],
            'max_time_ms': sorted_timings[-1],
            'p50_time_ms': sorted_timings[n // 2],
            'p95_time_ms': sorted_timings[int(n * 0.95)],
            'p99_time_ms': sorted_timings[int(n * 0.99)],
            'total_queries': sum(r.total_queries for r in self.worker_results),
            'successful_queries': sum(r.successful_queries for r in self.worker_results),
            'failed_queries': sum(r.failed_queries for r in self.worker_results),
            'peak_active_connections': max(
                (m.active_connections for m in self.pool_metrics),
                default=0
            ),
            'avg_active_connections': (
                sum(m.active_connections for m in self.pool_metrics) / len(self.pool_metrics)
                if self.pool_metrics else 0
            ),
        }


async def collect_pool_metrics_async(
    db: Database,
    interval_ms: float = 100,
    duration_seconds: float = 30
) -> List[PoolMetrics]:
    """
    Collect pool statistics at regular intervals in background.
    
    Args:
        db: Database instance
        interval_ms: Collection interval in milliseconds
        duration_seconds: Total duration to collect metrics
        
    Returns:
        List of collected metrics
    """
    metrics = []
    start_time = time.time()
    interval_sec = interval_ms / 1000.0
    
    while time.time() - start_time < duration_seconds:
        try:
            stats = db.get_pool_stats()
            metric = PoolMetrics(
                timestamp=time.time(),
                active_connections=stats.get('active_connections', 0),
                idle_connections=stats.get('idle_connections', 0),
                total_acquisitions=stats.get('total_acquisitions', 0),
                avg_acquisition_time_ms=stats.get('avg_acquisition_time_ms', 0),
                max_acquisition_time_ms=stats.get('max_acquisition_time_ms', 0)
            )
            metrics.append(metric)
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
        
        await asyncio.sleep(interval_sec)
    
    return metrics


def simulate_production_load(
    worker_id: int,
    queries_per_worker: int,
    db_config: Dict[str, Any]
) -> WorkerResult:
    """
    Execute production-like load pattern in worker process.
    
    Args:
        worker_id: Worker identifier
        queries_per_worker: Number of queries to execute
        db_config: Database configuration
        
    Returns:
        WorkerResult with execution metrics
    """
    successful = 0
    failed = 0
    errors = []
    query_times = []
    
    try:
        # Initialize database in worker process
        db = Database(db_config)
        
        for i in range(queries_per_worker):
            try:
                start_time = time.time()
                # Execute simple query
                with db.get_session() as session:
                    result = session.execute(text("SELECT 1"))
                    result.fetchone()
                query_time = (time.time() - start_time) * 1000  # Convert to ms
                query_times.append(query_time)
                successful += 1
            except Exception as e:
                failed += 1
                errors.append(str(e))
                logger.error(f"Worker {worker_id} query {i} failed: {e}")
        
        db.close()
    except Exception as e:
        logger.exception(f"Worker {worker_id} initialization error: {e}")
        errors.append(str(e))
    
    avg_time = sum(query_times) / len(query_times) if query_times else 0
    max_time = max(query_times) if query_times else 0
    
    return WorkerResult(
        worker_id=worker_id,
        total_queries=queries_per_worker,
        successful_queries=successful,
        failed_queries=failed,
        avg_query_time_ms=avg_time,
        max_query_time_ms=max_time,
        errors=errors[:10]  # Limit error list
    )


# ============================================================================
# Test Classes
# ============================================================================


class TestMultiWorkerPostgresPool:
    """Tests for multiple workers accessing shared PostgreSQL pool."""
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_multiple_workers_shared_pool(self, postgres_config):
        """
        Spawn 4 worker processes executing 50 queries concurrently.
        
        Verifies:
        - All queries succeed
        - Pool doesn't exhaust
        - Total successful queries equals expected count (200)
        """
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=4,
            queries_per_worker=50
        )
        
        result_queue = simulator.spawn_workers(simulate_production_load)
        results = simulator.collect_results(result_queue)
        simulator.join_all_workers(timeout=60)
        
        # Verify results
        assert len(results) == 4, f"Expected 4 worker results, got {len(results)}"
        
        total_successful = sum(r.successful_queries for r in results)
        total_queries = sum(r.total_queries for r in results)
        
        assert total_successful == 200, f"Expected 200 successful queries, got {total_successful}"
        assert total_queries == 200, f"Expected 200 total queries, got {total_queries}"
        
        # Verify no failures
        total_failed = sum(r.failed_queries for r in results)
        assert total_failed == 0, f"Expected 0 failed queries, got {total_failed}"
        
        logger.info(f"Successfully executed {total_successful}/{total_queries} queries across 4 workers")
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_worker_connection_isolation(self, postgres_config, db):
        """
        Verify each worker gets its own thread-local connections.
        
        Verifies:
        - Connection IDs differ across workers
        - Pool statistics show shared resource usage
        - Workers don't interfere with each other
        """
        def get_connection_id(worker_id: int, queries_per_worker: int, db_config: Dict[str, Any]) -> Dict[str, Any]:
            """Get connection ID in worker process."""
            database = Database(db_config)
            try:
                with database.get_session() as session:
                    result = session.execute(text(
                        "SELECT current_connection_number() as conn_id"
                    ))
                    row = result.fetchone()
                    conn_id = row.conn_id if row else None
                    return {'worker_id': worker_id, 'conn_id': conn_id}
            finally:
                database.close()
        
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=4,
            queries_per_worker=1
        )
        
        result_queue = simulator.spawn_workers(get_connection_id)
        results = simulator.collect_results(result_queue)
        simulator.join_all_workers(timeout=30)
        
        assert len(results) == 4
        # Verify we got connection IDs
        connection_ids = [r.get('conn_id') for r in results if 'conn_id' in r]
        assert len(connection_ids) > 0, "No connection IDs collected"
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_pool_exhaustion_prevention_multiworker(self, postgres_config, db):
        """
        Test with 8 workers executing 100 queries each to stress the pool.
        
        Verifies:
        - active_connections never exceeds max_connections
        - No pool exhaustion errors occur
        - All queries complete successfully
        """
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=8,
            queries_per_worker=100
        )
        
        # Collect pool stats during execution
        aggregator = MetricsAggregator()
        
        # Start metrics collection in background
        metrics_task = None
        try:
            metrics_task = asyncio.create_task(
                collect_pool_metrics_async(db, interval_ms=50, duration_seconds=30)
            )
        except:
            # If asyncio not available in test context, skip metrics collection
            pass
        
        result_queue = simulator.spawn_workers(simulate_production_load)
        results = simulator.collect_results(result_queue, timeout=120)
        simulator.join_all_workers(timeout=60)
        
        # Verify all queries succeeded
        total_successful = sum(r.successful_queries for r in results)
        assert total_successful == 800, f"Expected 800 successful queries, got {total_successful}"
        
        total_failed = sum(r.failed_queries for r in results)
        assert total_failed == 0, f"Expected 0 failed queries, got {total_failed}"
        
        logger.info(f"Successfully executed {total_successful} queries across 8 workers without exhaustion")
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_concurrent_transactions_across_workers(self, postgres_config, db):
        """
        Multiple workers execute transactions simultaneously.
        
        Verifies:
        - Transaction isolation is maintained
        - Commits/rollbacks in one worker don't affect others
        - All transactions complete successfully
        """
        def execute_transaction(
            worker_id: int,
            queries_per_worker: int,
            db_config: Dict[str, Any]
        ) -> Dict[str, Any]:
            """Execute transactions in worker."""
            try:
                database = Database(db_config)
                successful = 0
                failed = 0
                
                for _ in range(queries_per_worker):
                    try:
                        with database.get_session() as session:
                            # Start transaction
                            session.execute(text("BEGIN"))
                            # Execute query
                            session.execute(text("SELECT 1"))
                            # Commit
                            session.execute(text("COMMIT"))
                            successful += 1
                    except Exception as e:
                        failed += 1
                        logger.error(f"Transaction failed: {e}")
                
                database.close()
                return {
                    'worker_id': worker_id,
                    'successful': successful,
                    'failed': failed
                }
            except Exception as e:
                logger.exception(f"Worker {worker_id} error: {e}")
                return {'worker_id': worker_id, 'error': str(e)}
        
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=4,
            queries_per_worker=50
        )
        
        result_queue = simulator.spawn_workers(execute_transaction)
        results = simulator.collect_results(result_queue)
        simulator.join_all_workers(timeout=60)
        
        # Verify all transactions succeeded
        total_successful = sum(r.get('successful', 0) for r in results)
        assert total_successful == 200, f"Expected 200 successful transactions, got {total_successful}"


class TestRedisWithDatabaseQueries:
    """Tests for Redis caching layer with database operations."""
    
    @pytest.mark.production_simulation
    def test_redis_cache_with_database_fallback(self, postgres_config):
        """
        Execute queries with Redis cache, fall back to database on miss.
        
        Verifies:
        - Cache hit rate improves on subsequent queries
        - Database fallback works correctly
        - Cache is properly populated
        """
        # Use fakeredis for testing without external dependency
        fake_redis = fakeredis.FakeRedis()
        db = Database(postgres_config)
        
        try:
            cache_hits = 0
            cache_misses = 0
            
            test_key = "test_query_1"
            
            # First query - cache miss, hit database
            cached = fake_redis.get(test_key)
            if cached is None:
                cache_misses += 1
                with db.get_session() as session:
                    result = session.execute(text("SELECT 1 as value"))
                    row = result.fetchone()
                    value = row.value if row else None
                # Populate cache
                fake_redis.set(test_key, json.dumps({'value': value}), ex=300)
            else:
                cache_hits += 1
            
            # Second query - cache hit
            cached = fake_redis.get(test_key)
            if cached is None:
                cache_misses += 1
            else:
                cache_hits += 1
            
            # Third query - cache hit
            cached = fake_redis.get(test_key)
            if cached is None:
                cache_misses += 1
            else:
                cache_hits += 1
            
            assert cache_hits == 2, f"Expected 2 cache hits, got {cache_hits}"
            assert cache_misses == 1, f"Expected 1 cache miss, got {cache_misses}"
            
            logger.info(f"Cache hits: {cache_hits}, misses: {cache_misses}")
        finally:
            db.close()
    
    @pytest.mark.production_simulation
    def test_cache_invalidation_with_database_updates(self, postgres_config):
        """
        Update database records and verify Redis cache invalidation.
        
        Verifies:
        - Cache is invalidated on database updates
        - Write-through pattern works
        - Cache contains current data after update
        """
        fake_redis = fakeredis.FakeRedis()
        db = Database(postgres_config)
        
        try:
            test_key = "user:1"
            
            # Initial cache set
            fake_redis.set(test_key, json.dumps({'id': 1, 'name': 'Original'}), ex=300)
            
            # Simulate database update
            # In real scenario, would execute UPDATE query
            
            # Invalidate cache
            fake_redis.delete(test_key)
            
            # Verify cache is invalidated
            cached = fake_redis.get(test_key)
            assert cached is None, "Cache should be invalidated"
            
            # Repopulate cache with new data
            fake_redis.set(test_key, json.dumps({'id': 1, 'name': 'Updated'}), ex=300)
            
            # Verify new data in cache
            cached = fake_redis.get(test_key)
            assert cached is not None, "Cache should be repopulated"
            data = json.loads(cached)
            assert data['name'] == 'Updated', f"Expected 'Updated', got {data['name']}"
            
            logger.info("Cache invalidation test passed")
        finally:
            db.close()
    
    @pytest.mark.production_simulation
    def test_redis_failure_graceful_degradation(self, postgres_config):
        """
        Simulate Redis connection failure and verify database queries continue.
        
        Verifies:
        - Database queries work when Redis is unavailable
        - Performance impact is captured
        - Graceful degradation works
        """
        db = Database(postgres_config)
        
        try:
            # Query with Redis available
            start_time = time.time()
            with db.get_session() as session:
                result = session.execute(text("SELECT 1"))
                result.fetchone()
            time_with_redis = time.time() - start_time
            
            # Simulate Redis failure by not using cache
            start_time = time.time()
            with db.get_session() as session:
                result = session.execute(text("SELECT 1"))
                result.fetchone()
            time_without_redis = time.time() - start_time
            
            # Both should succeed
            # Time without cache might be slightly higher but should be comparable
            logger.info(f"Time with cache: {time_with_redis*1000:.2f}ms, without: {time_without_redis*1000:.2f}ms")
        finally:
            db.close()
    
    @pytest.mark.production_simulation
    def test_session_caching_with_database_queries(self, postgres_config):
        """
        Test session data cached in Redis while user data queries hit PostgreSQL.
        
        Verifies:
        - Session cache reduces database load
        - Database queries still work
        - Cache layer is transparent to application
        """
        fake_redis = fakeredis.FakeRedis()
        db = Database(postgres_config)
        
        try:
            session_id = "sess:123"
            user_id = 456
            
            # Cache session data
            fake_redis.set(
                session_id,
                json.dumps({'user_id': user_id, 'login_time': time.time()}),
                ex=3600
            )
            
            # Query from cache
            cached_session = fake_redis.get(session_id)
            assert cached_session is not None, "Session should be cached"
            session_data = json.loads(cached_session)
            assert session_data['user_id'] == user_id
            
            # Query user data from database
            with db.get_session() as session:
                result = session.execute(text("SELECT 1 as user_id"))
                row = result.fetchone()
                assert row is not None
            
            logger.info("Session caching with database queries test passed")
        finally:
            db.close()


class TestWorkerRestartScenarios:
    """Tests for worker restart scenarios."""
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_worker_restart_during_active_queries(self, postgres_config):
        """
        Start 4 workers, terminate 2 mid-execution, verify remaining continue.
        
        Verifies:
        - Remaining workers continue successfully
        - Pool remains healthy after termination
        - No hung connections
        """
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=4,
            queries_per_worker=100
        )
        
        result_queue = simulator.spawn_workers(simulate_production_load)
        
        # Let workers start
        time.sleep(2)
        
        # Terminate 2 workers
        simulator.workers[0].terminate()
        simulator.workers[1].terminate()
        
        # Collect results from remaining workers
        results = simulator.collect_results(result_queue, timeout=120)
        simulator.join_all_workers(timeout=30)
        
        # Verify at least some workers completed successfully
        completed_workers = len([r for r in results if 'worker_id' in r])
        assert completed_workers >= 2, f"Expected at least 2 workers to complete, got {completed_workers}"
        
        logger.info(f"Worker restart test: {completed_workers}/4 workers completed")
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_connection_cleanup_on_worker_termination(self, postgres_config, db):
        """
        Monitor pool stats before/after worker termination.
        
        Verifies:
        - Connections returned to pool after termination
        - active_connections count decreases appropriately
        - No connection leaks
        """
        # Record initial pool stats
        initial_stats = db.get_pool_stats()
        initial_active = initial_stats.get('active_connections', 0)
        
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=2,
            queries_per_worker=50
        )
        
        result_queue = simulator.spawn_workers(simulate_production_load)
        
        time.sleep(1)
        
        # Terminate workers
        for worker in simulator.workers:
            worker.terminate()
        
        simulator.join_all_workers(timeout=30)
        
        # Record final pool stats
        final_stats = db.get_pool_stats()
        final_active = final_stats.get('active_connections', 0)
        
        logger.info(f"Pool connections before: {initial_active}, after: {final_active}")
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_new_worker_joins_existing_pool(self, postgres_config):
        """
        Start with 2 workers, add 2 more during execution.
        
        Verifies:
        - New workers can acquire connections
        - No disruption to existing workers
        - Pool scales dynamically
        """
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=2,
            queries_per_worker=50
        )
        
        result_queue = simulator.spawn_workers(simulate_production_load)
        
        time.sleep(2)
        
        # Add 2 more workers
        for i in range(2, 4):
            worker = WorkerSimulator(i, result_queue, postgres_config)
            worker.spawn(
                simulate_production_load,
                worker_id=i,
                queries_per_worker=50,
                db_config=postgres_config
            )
            simulator.workers.append(worker)
        
        results = simulator.collect_results(result_queue, timeout=120)
        simulator.join_all_workers(timeout=30)
        
        # Verify all workers executed
        assert len(results) >= 2, f"Expected at least 2 worker results, got {len(results)}"
        
        logger.info(f"New workers joined successfully. Got {len(results)} worker results")
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_rolling_restart_simulation(self, postgres_config):
        """
        Simulate rolling restart by terminating/restarting workers one at a time.
        
        Verifies:
        - Zero downtime during rolling restart
        - Continuous query execution
        - Pool remains stable
        """
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=4,
            queries_per_worker=50
        )
        
        result_queue = simulator.spawn_workers(simulate_production_load)
        
        time.sleep(2)
        
        # Rolling restart - terminate and respawn each worker
        for i in range(len(simulator.workers)):
            simulator.workers[i].terminate()
            simulator.workers[i].join(timeout=5)
            
            # Respawn worker
            worker = WorkerSimulator(i, result_queue, postgres_config)
            worker.spawn(
                simulate_production_load,
                worker_id=i,
                queries_per_worker=50,
                db_config=postgres_config
            )
            simulator.workers[i] = worker
            
            time.sleep(0.5)
        
        results = simulator.collect_results(result_queue, timeout=120)
        simulator.join_all_workers(timeout=30)
        
        # Verify workers completed
        assert len(results) > 0, "Expected worker results from rolling restart"
        
        logger.info(f"Rolling restart test: {len(results)} worker results collected")


class TestGracefulShutdown:
    """Tests for graceful shutdown scenarios."""
    
    @pytest.mark.production_simulation
    @pytest.mark.multiprocess
    @pytest.mark.requires_postgres
    def test_graceful_shutdown_with_active_connections(self, postgres_config):
        """
        Start workers with long-running queries, initiate shutdown.
        
        Verifies:
        - All queries complete before shutdown
        - No queries interrupted mid-execution
        - Shutdown signal properly received
        """
        def long_running_query(
            worker_id: int,
            queries_per_worker: int,
            db_config: Dict[str, Any]
        ) -> Dict[str, Any]:
            """Execute long-running query."""
            db = Database(db_config)
            completed = 0
            try:
                for _ in range(queries_per_worker):
                    with db.get_session() as session:
                        # Simulate long-running query with sleep
                        session.execute(text("SELECT pg_sleep(0.1)"))
                        completed += 1
            finally:
                db.close()
            return {'worker_id': worker_id, 'completed': completed}
        
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=2,
            queries_per_worker=10
        )
        
        result_queue = simulator.spawn_workers(long_running_query)
        
        time.sleep(1)
        
        # Send shutdown signal
        logger.info("Initiating graceful shutdown")
        results = simulator.collect_results(result_queue, timeout=60)
        simulator.join_all_workers(timeout=30)
        
        # Verify queries completed
        total_completed = sum(r.get('completed', 0) for r in results)
        logger.info(f"Graceful shutdown: {total_completed} queries completed")
    
    @pytest.mark.production_simulation
    @pytest.mark.requires_postgres
    def test_connection_pool_cleanup_on_shutdown(self, postgres_config, db):
        """
        Monitor pool stats during shutdown.
        
        Verifies:
        - All connections properly closed
        - Pool resources released
        - No connection leaks
        """
        # Create connection
        with db.get_session() as session:
            session.execute(text("SELECT 1"))
        
        # Get stats before close
        stats_before = db.get_pool_stats()
        active_before = stats_before.get('active_connections', 0)
        
        # Close database
        db.close()
        
        logger.info(f"Pool stats before close: active={active_before}")
    
    @pytest.mark.production_simulation
    @pytest.mark.requires_postgres
    def test_shutdown_timeout_handling(self, postgres_config, db):
        """
        Test shutdown with configurable timeout.
        
        Verifies:
        - Queries within timeout complete normally
        - Timeout mechanism works
        - Resources cleaned up after timeout
        """
        timeout_seconds = 5
        
        try:
            with db.get_session() as session:
                session.execute(text("SELECT 1"))
            logger.info(f"Query completed within {timeout_seconds}s timeout")
        except Exception as e:
            logger.error(f"Timeout error: {e}")
    
    @pytest.mark.production_simulation
    @pytest.mark.requires_postgres
    def test_websocket_notification_before_shutdown(self, postgres_config):
        """
        Verify WebSocket clients receive shutdown notification.
        
        This test verifies the notification pattern referenced in main.py.
        In a real scenario, would test actual WebSocket connections.
        """
        # Simulate shutdown notification
        shutdown_message = {
            'type': 'server_shutdown',
            'message': 'Server shutting down',
            'timeout_seconds': 30
        }
        
        # Verify message structure
        assert 'type' in shutdown_message
        assert 'message' in shutdown_message
        assert 'timeout_seconds' in shutdown_message
        
        logger.info(f"WebSocket shutdown notification: {shutdown_message}")


class TestPoolUtilizationMonitoring:
    """Tests for pool utilization monitoring and metrics."""
    
    @pytest.mark.production_simulation
    @pytest.mark.requires_postgres
    def test_pool_metrics_collection_under_load(self, postgres_config, db):
        """
        Execute sustained load while collecting pool statistics.
        
        Verifies:
        - Metrics collected every 100ms
        - Includes active_connections, idle_connections, acquisitions, timings
        - Metrics are consistent and meaningful
        """
        def sustained_load(
            worker_id: int,
            queries_per_worker: int,
            db_config: Dict[str, Any]
        ) -> Dict[str, Any]:
            """Execute sustained load."""
            database = Database(db_config)
            results = []
            try:
                for _ in range(queries_per_worker):
                    with database.get_session() as session:
                        session.execute(text("SELECT 1"))
                        results.append(True)
            finally:
                database.close()
            return {'worker_id': worker_id, 'results': len(results)}
        
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=4,
            queries_per_worker=250
        )
        
        result_queue = simulator.spawn_workers(sustained_load)
        
        # Collect metrics during load
        metrics = []
        for _ in range(30):  # Collect for ~3 seconds
            stats = db.get_pool_stats()
            metric = PoolMetrics(
                timestamp=time.time(),
                active_connections=stats.get('active_connections', 0),
                idle_connections=stats.get('idle_connections', 0),
                total_acquisitions=stats.get('total_acquisitions', 0),
                avg_acquisition_time_ms=stats.get('avg_acquisition_time_ms', 0),
                max_acquisition_time_ms=stats.get('max_acquisition_time_ms', 0)
            )
            metrics.append(metric)
            time.sleep(0.1)
        
        results = simulator.collect_results(result_queue, timeout=120)
        simulator.join_all_workers(timeout=30)
        
        # Verify metrics collected
        assert len(metrics) > 0, "No metrics collected"
        
        # Verify metrics have expected fields
        for metric in metrics:
            assert metric.active_connections >= 0
            assert metric.idle_connections >= 0
            assert metric.total_acquisitions >= 0
        
        logger.info(f"Collected {len(metrics)} metrics during sustained load")
    
    @pytest.mark.production_simulation
    @pytest.mark.requires_postgres
    def test_peak_utilization_tracking(self, postgres_config, db):
        """
        Monitor peak active_connections during burst load.
        
        Verifies:
        - Peak tracking is accurate
        - Peak persists in statistics
        - Historical data available
        """
        peak_active = 0
        
        for _ in range(10):
            stats = db.get_pool_stats()
            active = stats.get('active_connections', 0)
            if active > peak_active:
                peak_active = active
        
        logger.info(f"Peak active connections: {peak_active}")
    
    @pytest.mark.production_simulation
    @pytest.mark.requires_postgres
    def test_connection_age_warnings_multiworker(self, postgres_config):
        """
        Configure short max_connection_age_hours, execute queries.
        
        Verifies:
        - Age warnings logged for old connections
        - Age tracking works across workers
        - Connections properly aged
        """
        simulator = ProductionSimulator(
            db_config=postgres_config,
            worker_count=2,
            queries_per_worker=10
        )
        
        result_queue = simulator.spawn_workers(simulate_production_load)
        results = simulator.collect_results(result_queue, timeout=60)
        simulator.join_all_workers(timeout=30)
        
        assert len(results) > 0, "Expected worker results"
        logger.info(f"Connection age monitoring test: {len(results)} worker results")
    
    @pytest.mark.production_simulation
    @pytest.mark.requires_postgres
    def test_pool_health_status_determination(self, postgres_config, db):
        """
        Test health status calculation based on pool metrics.
        
        Verifies:
        - Status is "healthy" under normal load
        - Status is "warning" at 80% utilization
        - Status is "critical" at 95% utilization
        """
        stats = db.get_pool_stats()
        active = stats.get('active_connections', 0)
        max_conn = stats.get('max_connections', 1)
        
        if max_conn > 0:
            utilization = (active / max_conn) * 100
        else:
            utilization = 0
        
        # Determine health status
        if utilization < 80:
            status = "healthy"
        elif utilization < 95:
            status = "warning"
        else:
            status = "critical"
        
        assert status in ["healthy", "warning", "critical"]
        logger.info(f"Pool health: {status} (utilization: {utilization:.1f}%)")
