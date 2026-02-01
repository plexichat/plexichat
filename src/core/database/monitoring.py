import threading
import time
import uuid
from typing import Any, List, Optional, Tuple, Dict, Union
from datetime import datetime
import utils.config as config
import utils.logger as logger

class DatabaseMonitor:
    """Handles database monitoring, metrics collection, and background logging."""

    def __init__(self, db_config: Dict[str, Any], db_type: str):
        self.db_config = db_config
        self.db_type = db_type
        self._lock = threading.RLock()
        
        # Metrics state
        self._connection_acquisitions = []  # List of (timestamp, duration) tuples
        self._connection_pool_waits = []  # List of (timestamp, wait_duration) tuples
        self._connection_metadata = {}  # Track connection creation times and info
        self._query_execution_times = []  # List of (timestamp, execution_time_ms) tuples
        self._error_counts = {}  # Dictionary of {error_type: [(timestamp, error_type)]}
        
        # Threading state
        self._periodic_logging_thread = None
        self._stop_logging = False
        
        # Configuration
        monitoring_config = config.get("monitoring", {})
        alert_thresholds = monitoring_config.get("alert_thresholds", {})
        
        max_connection_age_hours = self.db_config.get("connection_pool", {}).get("max_connection_age_hours", 0.5)
        self._max_connection_age_seconds = max_connection_age_hours * 3600
        self._periodic_log_interval = monitoring_config.get("log_interval", monitoring_config.get("log_interval_seconds", 60))
        self._slow_query_threshold_ms = alert_thresholds.get("query_time_ms", 5000)
        self._error_rate_window_seconds = 60
        self._error_rate_threshold = alert_thresholds.get("db_errors_per_minute", 10)
        self._pool_saturation_threshold_percent = alert_thresholds.get("db_pool_saturation_percent", 75)

    def record_acquisition(self, duration: float):
        with self._lock:
            self._connection_acquisitions.append((time.time(), duration))

    def record_pool_wait(self, duration: float):
        with self._lock:
            self._connection_pool_waits.append((time.time(), duration))

    def record_query_execution(self, execution_time_ms: float):
        with self._lock:
            self._query_execution_times.append((time.time(), execution_time_ms))

    def record_error(self, error_type: str):
        with self._lock:
            if error_type not in self._error_counts:
                self._error_counts[error_type] = []
            self._error_counts[error_type].append((time.time(), error_type))

    def add_connection_metadata(self, conn_id: int, metadata: Dict[str, Any]):
        with self._lock:
            self._connection_metadata[conn_id] = metadata

    def remove_connection_metadata(self, conn_id: int):
        with self._lock:
            if conn_id in self._connection_metadata:
                del self._connection_metadata[conn_id]

    def get_connection_metadata(self, conn_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._connection_metadata.get(conn_id)

    def update_connection_last_used(self, conn_id: int):
        with self._lock:
            if conn_id in self._connection_metadata:
                self._connection_metadata[conn_id]["last_used"] = time.time()

    def get_pool_stats(self, engine_stats: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            stats = {
                **engine_stats,
                "avg_acquisition_time": 0.0,
                "max_acquisition_time": 0.0,
                "avg_pool_wait_time": 0.0,
                "total_acquisitions": len(self._connection_acquisitions),
                "total_pool_waits": len(self._connection_pool_waits),
                "old_connections": [],
                "status": engine_stats.get("status", "healthy"),
                "database_type": self.db_type,
                "timestamp": datetime.now().isoformat(),
            }
            
            # Acquisition time metrics
            if self._connection_acquisitions:
                acq_times = [duration for _, duration in self._connection_acquisitions[-100:]]
                stats["avg_acquisition_time"] = sum(acq_times) / len(acq_times)
                stats["max_acquisition_time"] = max(acq_times)
            
            # Pool wait metrics
            if self._connection_pool_waits:
                wait_times = [duration for _, duration in self._connection_pool_waits[-100:]]
                stats["avg_pool_wait_time"] = sum(wait_times) / len(wait_times)
            
            # Check for old connections
            current_time = time.time()
            for conn_id, metadata in self._connection_metadata.items():
                if "created_at" in metadata:
                    age_seconds = current_time - metadata["created_at"]
                    if self._max_connection_age_seconds > 0 and age_seconds > self._max_connection_age_seconds:
                        stats["old_connections"].append({
                            "connection_id": conn_id,
                            "age_seconds": age_seconds,
                            "thread_id": metadata.get("thread_id"),
                        })
            
            # Status determination
            util = engine_stats.get("utilization_percent", 0)
            if isinstance(util, (int, float)):
                if util >= 90:
                    stats["status"] = "critical"
                elif util >= 75:
                    stats["status"] = "warning"

            return stats

    def calculate_error_rate(self) -> float:
        """Calculate current error rate (errors per minute)."""
        with self._lock:
            window_start = time.time() - self._error_rate_window_seconds
            recent_errors = 0
            for error_list in self._error_counts.values():
                recent_errors += sum(1 for ts, _ in error_list if ts >= window_start)
            
            return (recent_errors / self._error_rate_window_seconds) * 60

    def start_pool_monitoring(self, get_stats_cb, reap_cb=None):
        """Start the background logging thread."""
        with self._lock:
            if self._periodic_logging_thread and self._periodic_logging_thread.is_alive():
                return

            self._stop_logging = False
            self._periodic_logging_thread = threading.Thread(
                target=self._periodic_logging_loop,
                args=(get_stats_cb, reap_cb),
                name="DatabasePoolMonitor",
                daemon=True
            )
            self._periodic_logging_thread.start()
            logger.info(f"Started pool monitoring thread (interval: {self._periodic_log_interval}s)")

    def stop_pool_monitoring(self):
        """Stop the background logging thread."""
        with self._lock:
            self._stop_logging = True
            
    def _format_log_context(self, **kwargs) -> str:
        """Internal helper for structured logging context."""
        pairs = [f"{k}={v}" for k, v in kwargs.items() if v is not None]
        return f"[{' '.join(pairs)}]" if pairs else ""

    def _periodic_logging_loop(self, get_stats_cb, reap_cb=None):
        """Main loop for the periodic logging thread."""
        while not self._stop_logging:
            try:
                # 1. Proactively reap leaked connections if callback provided
                if reap_cb:
                    try:
                        reaped = reap_cb()
                        if reaped > 0:
                            logger.info(f"Pool maintenance: reaped {reaped} leaked/idle connections")
                    except Exception as reap_err:
                        logger.error(f"Error during connection reaping: {reap_err}")

                # 2. Collect and log statistics
                stats = get_stats_cb()
                error_rate = self.calculate_error_rate()
                
                context = self._format_log_context(
                    active_connections=stats.get('active_connections'),
                    idle_connections=stats.get('idle_connections'),
                    total_connections=stats.get('total_connections'),
                    max_connections=stats.get('max_connections'),
                    utilization_percent=f"{stats.get('utilization_percent', 0):.1f}" if isinstance(stats.get('utilization_percent'), (int, float)) else stats.get('utilization_percent'),
                    avg_acquisition_time_ms=f"{stats.get('avg_acquisition_time', 0) * 1000:.2f}",
                    error_rate_per_minute=f"{error_rate:.2f}"
                )
                
                logger.info(f"Pool monitoring statistics {context}")
                
                # Check for high utilization or error rate
                util = stats.get('utilization_percent', 0)
                if isinstance(util, (int, float)) and util > self._pool_saturation_threshold_percent:
                    logger.warning(f"High pool utilization detected {self._format_log_context(utilization_percent=f'{util:.1f}', threshold=self._pool_saturation_threshold_percent)}")
                
                if error_rate > self._error_rate_threshold:
                    logger.warning(f"High error rate detected {self._format_log_context(error_rate=f'{error_rate:.2f}', threshold=self._error_rate_threshold)}")
                
                # Old connections warning
                for old in stats.get("old_connections", []):
                    logger.warning(f"Long-lived connection detected - ID: {old['connection_id']}, Age: {old['age_seconds']:.1f}s")
                
                self._cleanup_metrics()
                
            except Exception as e:
                logger.error(f"Error in pool monitoring loop: {e}")
            
            time.sleep(self._periodic_log_interval)

    def _cleanup_metrics(self):
        """Clean up old metrics to prevent memory growth."""
        with self._lock:
            now = time.time()
            window_start = now - self._error_rate_window_seconds
            
            # Trim error counts
            for error_type in list(self._error_counts.keys()):
                self._error_counts[error_type] = [(ts, et) for ts, et in self._error_counts[error_type] if ts >= window_start]
                if not self._error_counts[error_type]:
                    del self._error_counts[error_type]
            
            # Trim other metrics to last 1000
            if len(self._query_execution_times) > 1000:
                self._query_execution_times = self._query_execution_times[-1000:]
            if len(self._connection_acquisitions) > 1000:
                self._connection_acquisitions = self._connection_acquisitions[-1000:]
            if len(self._connection_pool_waits) > 1000:
                self._connection_pool_waits = self._connection_pool_waits[-1000:]

    def check_connection_age(self, conn_id: int) -> None:
        """Check if a connection has exceeded its maximum age."""
        with self._lock:
            metadata = self._connection_metadata.get(conn_id)
            if metadata and "created_at" in metadata:
                age = time.time() - metadata["created_at"]
                if self._max_connection_age_seconds > 0 and age > self._max_connection_age_seconds:
                    logger.warning(f"Connection {conn_id} has exceeded max age threshold ({age:.1f}s)")