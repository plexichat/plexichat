"""
Database Cloner - Utility for migrating data between different database engines.

This module provides the DataCloner class which handles copying data from
a source database (typically SQLite) to a target database (typically PostgreSQL)
while maintaining integrity, handling sequences, and coordinating with the
migration system.
"""

import time
from typing import List, Dict, Any, Optional

import utils.logger as logger
from src.core.database import Database


class DataCloner:
    """Handles cloning data from one database instance to another."""

    def __init__(self, source_db: Database, target_db: Database):
        """
        Initialize the cloner.

        Args:
            source_db: The database instance to copy data FROM
            target_db: The database instance to copy data TO
        """
        self.source = source_db
        self.target = target_db
        self.batch_size = 1000

        # Internal state
        self._table_list: List[str] = []
        self._stats: Dict[str, Dict[str, Any]] = {}

    def validate_source_status(self) -> bool:
        """
        Validate the status of the source database.

        Ensures all migrations are applied and there are no failed migrations.

        Returns:
            True if source is ready for cloning
        """
        from . import get_status

        status = get_status(self.source)

        if status["pending_count"] > 0:
            logger.error(
                f"Source database has {status['pending_count']} pending migrations. Apply them first."
            )
            return False

        if status["failed_count"] > 0:
            logger.error(
                f"Source database has {status['failed_count']} failed migrations. Fix them first."
            )
            return False

        logger.info(
            f"Source database validated: {status['applied_count']} migrations applied."
        )
        return True

    def get_tables(self) -> List[str]:
        """
        Discover all user tables in the source database.

        Returns:
            List of table names
        """
        if self.source.type == "sqlite":
            rows = self.source.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            # Filter out internal/tracking tables that shouldn't be cloned directly or are handled specially
            exclude = {"migration_lock", "migration_uptime"}
            self._table_list = [
                row["name"] for row in rows if row["name"] not in exclude
            ]
        else:
            # Fallback for other source types if needed
            rows = self.source.fetch_all(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
            self._table_list = [row["table_name"] for row in rows]

        logger.info(f"Discovered {len(self._table_list)} tables in source database.")
        return self._table_list

    def clone_table(self, table_name: str) -> Dict[str, Any]:
        """
        Clone a single table from source to target.

        Args:
            table_name: Name of the table to clone

        Returns:
            Stats for the cloning operation
        """
        start_time = time.time()
        logger.info(f"Cloning table: {table_name}")

        # 1. Get column information from source
        if self.source.type == "sqlite":
            # Sanitize table name for PRAGMA (not parameterized)
            safe_table = f"`{table_name}`"
            columns_info = self.source.fetch_all(f"PRAGMA table_info({safe_table})")
            column_names = [row["name"] for row in columns_info]
        else:
            # Postgres info_schema
            columns_info = self.source.fetch_all(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                (table_name,),
            )
            column_names = [row["column_name"] for row in columns_info]

        if not column_names:
            logger.warning(f"No columns found for table {table_name}, skipping.")
            return {"rows": 0, "status": "skipped"}

        # 2. Count total rows
        count_row = self.source.fetch_one(f"SELECT COUNT(*) as count FROM {table_name}")
        total_rows = count_row["count"] if count_row else 0

        if total_rows == 0:
            logger.info(f"Table {table_name} is empty, skipping data copy.")
            return {"rows": 0, "status": "empty"}

        # 3. Truncate target table
        # We use DELETE FROM instead of TRUNCATE for better cross-DB compatibility
        # and to handle FK constraints if they exist (though we'll disable triggers)
        self.target.execute(f"DELETE FROM {table_name}")

        # 4. Copy data in batches
        rows_copied = 0
        placeholders = ", ".join(["?"] * len(column_names))
        cols_str = ", ".join(
            [
                f'"{c}"' if self.target.type == "postgres" else f"`{c}`"
                for c in column_names
            ]
        )
        insert_sql = f"INSERT INTO {table_name} ({cols_str}) VALUES ({placeholders})"

        # Use a generator/offset approach for batching
        offset = 0
        while offset < total_rows:
            batch = self.source.fetch_all(
                f"SELECT * FROM {table_name} LIMIT ? OFFSET ?",
                (self.batch_size, offset),
            )

            if not batch:
                break

            # Convert rows to tuples in correct column order
            values = []
            for row in batch:
                values.append(tuple(row[col] for col in column_names))

            self.target.execute_many(insert_sql, values)
            rows_copied += len(batch)
            offset += self.batch_size
            logger.debug(f"  Progress: {rows_copied}/{total_rows}")

        duration = time.time() - start_time
        logger.info(
            f"Successfully cloned {rows_copied} rows from {table_name} in {duration:.2f}s"
        )

        # 5. Reset sequence for PostgreSQL if it has a serial ID
        if self.target.type == "postgres" and "id" in column_names:
            try:
                self.target.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), COALESCE(MAX(id), 1)) FROM {table_name}"
                )
                logger.debug(f"  Reset sequence for {table_name}.id")
            except Exception as e:
                # Table might not have a serial sequence on 'id'
                logger.debug(f"  Note: Could not reset sequence for {table_name}: {e}")

        stats = {"rows": rows_copied, "duration": duration, "status": "completed"}
        self._stats[table_name] = stats
        return stats

    def clone_all(self, tables: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Clone all specified tables.

        Args:
            tables: Optional list of tables to clone (defaults to all discovered)

        Returns:
            Summary of cloning results
        """
        if tables is None:
            tables = self.get_tables()

        start_time = time.time()
        logger.info(f"Starting cloning of {len(tables)} tables...")

        # For Postgres, we should disable triggers during mass insert for performance
        # and to avoid FK issues if we're not inserting in perfect dependency order
        if self.target.type == "postgres":
            # This requires superuser or table ownership
            self.target.execute("SET session_replication_role = 'replica';")

        try:
            for table in tables:
                self.clone_table(table)
        finally:
            if self.target.type == "postgres":
                self.target.execute("SET session_replication_role = 'origin';")

        # Specialized module initialization
        self._initialize_postgres_modules()

        total_duration = time.time() - start_time
        total_rows = sum(s["rows"] for s in self._stats.values())

        summary = {
            "success": True,
            "table_count": len(self._stats),
            "total_rows": total_rows,
            "duration": total_duration,
            "tables": self._stats,
        }

        logger.info(
            f"Cloning complete. {total_rows} total rows across {len(self._stats)} tables in {total_duration:.2f}s"
        )
        return summary

    def _initialize_postgres_modules(self):
        """Perform engine-specific initialization for modules like search."""
        if self.target.type != "postgres":
            return

        logger.info("Performing PostgreSQL-specific module initialization...")

        # 1. Search Indexer
        try:
            from src.core.search.indexer.postgres import PostgresIndexer

            indexer = PostgresIndexer(self.target)
            indexer.initialize()
            logger.info(
                "  PostgreSQL search indexer initialized (triggers and indexes created)"
            )
        except Exception as e:
            logger.error(f"  Failed to initialize search indexer: {e}")

        # 2. Voice settings (if applicable)
        # Any other modules that have custom initialization logic should go here

    def verify_counts(self) -> Dict[str, Any]:
        """
        Verify that row counts match between source and target for all cloned tables.

        Returns:
            Verification results
        """
        logger.info("Verifying row counts...")
        mismatches = []
        verified_count = 0

        for table in self._stats.keys():
            s_count = self.source.fetch_one(f"SELECT COUNT(*) as count FROM {table}")[
                "count"
            ]
            t_count = self.target.fetch_one(f"SELECT COUNT(*) as count FROM {table}")[
                "count"
            ]

            if s_count != t_count:
                mismatches.append(
                    {"table": table, "source": s_count, "target": t_count}
                )
            else:
                verified_count += 1

        result = {
            "valid": len(mismatches) == 0,
            "verified_count": verified_count,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }

        if result["valid"]:
            logger.info(f"Verification SUCCESS: All {verified_count} tables match.")
        else:
            logger.error(
                f"Verification FAILED: {len(mismatches)} tables have row count mismatches!"
            )
            for m in mismatches:
                logger.error(
                    f"  {m['table']}: Source={m['source']}, Target={m['target']}"
                )

        return result
