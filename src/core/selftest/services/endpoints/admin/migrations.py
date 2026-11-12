import time

import utils.logger as logger

from .base import AdminMixinBase


class MigrationMixin(AdminMixinBase):
    """Migration apply/rollback coverage for legacy and new endpoints."""

    def test_migration_endpoints(self) -> None:
        """Test migration apply and rollback using the 001 selftest no-op migration.

        Approval-gated: each execution is preceded by the real multi-admin
        ceremony (request + 2 approvals), so the gates are exercised.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping migration tests (no auth token)")
            return

        session = self.ctx.session
        mig_path_apply = "/api/v1/admin/database/migrations/apply/001"
        mig_path_rollback = "/api/v1/admin/database/migrations/rollback/001"

        # Migration 001 may already be applied during server startup.
        # Rollback first so we can test apply; then rollback again to leave
        # the DB in the same state we found it.
        logger.info(
            "Testing POST /api/v1/admin/database/migrations/rollback/001 (pre-test)..."
        )
        if not self._approval_ceremony("migrations.rollback"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_path_rollback,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "migration_rollback_pre",
                    "error": "approval ceremony failed",
                }
            )
            return
        rb1_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_rollback}",
            timeout=10,
        )
        rb1_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_rollback,
                "status_code": resp.status_code,
                "duration_ms": (time.time() - rb1_start) * 1000,
                "success": rb1_ok,
                "label": "migration_rollback_pre",
            }
        )
        if rb1_ok:
            logger.info(
                f"Migration rollback 001 (pre-test) PASSED -> {resp.status_code}"
            )
        else:
            logger.debug(
                f"Migration rollback 001 (pre-test) -> {resp.status_code}: {resp.text[:200]}"
            )

        # --- Apply migration 001 ---
        logger.info("Testing POST /api/v1/admin/database/migrations/apply/001...")
        if not self._approval_ceremony("migrations.run"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_path_apply,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "migration_apply",
                    "error": "approval ceremony failed",
                }
            )
            return
        mig_apply_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_apply}",
            timeout=10,
        )
        duration = (time.time() - mig_apply_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_apply,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "migration_apply",
            }
        )
        if success:
            logger.info(f"Migration apply 001 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration apply 001 -> {resp.status_code}: {resp.text[:200]}"
            )

        # --- Rollback migration 001 ---
        logger.info("Testing POST /api/v1/admin/database/migrations/rollback/001...")
        if not self._approval_ceremony("migrations.rollback"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_path_rollback,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "migration_rollback",
                    "error": "approval ceremony failed",
                }
            )
            return
        mig_rollback_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_rollback}",
            timeout=10,
        )
        duration = (time.time() - mig_rollback_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_rollback,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "migration_rollback",
            }
        )
        if success:
            logger.info(f"Migration rollback 001 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration rollback 001 -> {resp.status_code}: {resp.text[:200]}"
            )

    def test_new_migration_endpoints(self) -> None:
        """Test the new migration run/rollback endpoints (admin/migrations.py).

        Unlike the legacy /admin/database/migrations/* endpoints tested in
        test_migration_endpoints(), these use the richer MigrationRunRequest
        body with dry_run support and irreversible-migration checks. The run
        applies 001 for real (the rollback step needs it applied); both are
        approval-gated: the real multi-admin ceremony precedes each.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping new migration tests (no auth token)")
            return

        session = self.ctx.session
        mig_run = "/api/v1/admin/migrations/001/run"
        mig_rollback = "/api/v1/admin/migrations/001/rollback"

        # --- Run (apply) migration 001 via new endpoint ---
        # Applied for real (not dry-run): the rollback step below needs an
        # applied 001 to roll back, and the legacy test may have left it
        # rolled back. Net DB effect across both tests is zero.
        logger.info("Testing POST /api/v1/admin/migrations/001/run (apply)...")
        if not self._approval_ceremony("migrations.run"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_run,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "new_migration_apply",
                    "error": "approval ceremony failed",
                }
            )
            return
        run_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_run}",
            json={"dry_run": False},
            timeout=10,
        )
        duration = (time.time() - run_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_run,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "new_migration_apply",
            }
        )
        if success:
            logger.info(f"Migration apply 001 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration apply 001 -> {resp.status_code}: {resp.text[:200]}"
            )

        # --- Rollback 001 via new endpoint ---
        logger.info("Testing POST /api/v1/admin/migrations/001/rollback...")
        if not self._approval_ceremony("migrations.rollback"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_rollback,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "new_migration_rollback",
                    "error": "approval ceremony failed",
                }
            )
            return
        rb_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_rollback}",
            timeout=10,
        )
        duration = (time.time() - rb_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_rollback,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "new_migration_rollback",
            }
        )
        if success:
            logger.info(f"Migration rollback 001 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration rollback 001 -> {resp.status_code}: {resp.text[:200]}"
            )
