import time

import utils.logger as logger

from .base import AdminMixinBase


class DsarMixin(AdminMixinBase):
    """DSAR export coverage."""

    def test_dsar(self) -> None:
        """Test the full user-facing data export (DSAR) workflow.

        Mirrors the patterns used by `test_delay_deletion` but for the
        right-to-portability flow. We avoid admin-only endpoints because
        the user is the one driving the request, and we want to make sure
        the self-test exercises the same path real users hit.

        Steps:
            1. User requests a data export.
            2. User lists their data export requests.
            3. User fetches a single request status.
            4. User cancels the request (cleanup).
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping DSAR tests (no auth token)")
            return

        session = self.ctx.session
        test_pass = self.ctx._test_password
        assert test_pass, "self-test password must be set up before requests"

        dsar_request_id = None

        # Step 1: User requests a data export.
        logger.info(
            "Testing POST /api/v1/users/@me/data-export (request data export)..."
        )
        request_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export",
            json={"password": test_pass, "format": "json"},
            timeout=10,
        )
        duration = (time.time() - request_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/users/@me/data-export",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "dsar_request",
            }
        )
        if success:
            logger.info(f"DSAR request PASSED -> {resp.status_code}")
        else:
            logger.warning(f"DSAR request -> {resp.status_code}: {resp.text[:200]}")
            return  # nothing else to test if the request itself failed

        # Step 2: User lists their data export requests.
        logger.info("Testing GET /api/v1/users/@me/data-export (list)...")
        list_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export",
            timeout=10,
        )
        duration = (time.time() - list_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": "/api/v1/users/@me/data-export",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "dsar_list",
            }
        )
        if success:
            try:
                data = resp.json()
                items = data.get("requests", [])
                if items:
                    dsar_request_id = items[0].get("id")
                    if dsar_request_id:
                        self.ctx.test_dsar_id = int(dsar_request_id)
                        logger.debug(f"Found DSAR request id: {dsar_request_id}")
            except Exception:
                pass
            logger.info(f"DSAR list PASSED -> {resp.status_code}")
        else:
            logger.warning(f"DSAR list -> {resp.status_code}: {resp.text[:200]}")

        if not dsar_request_id and self.ctx.test_dsar_id:
            dsar_request_id = self.ctx.test_dsar_id

        if not dsar_request_id:
            return

        # Step 3: User fetches a single request status.
        logger.info(
            f"Testing GET /api/v1/users/@me/data-export/{dsar_request_id} (status)..."
        )
        status_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export/{dsar_request_id}",
            timeout=10,
        )
        duration = (time.time() - status_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": f"/api/v1/users/@me/data-export/{dsar_request_id}",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "dsar_status",
            }
        )
        if success:
            logger.info(f"DSAR status PASSED -> {resp.status_code}")
        else:
            logger.warning(f"DSAR status -> {resp.status_code}: {resp.text[:200]}")

        # Step 4: User cancels the request (cleanup).

        # Step 4: User cancels the request (cleanup).
        logger.info(
            f"Testing DELETE /api/v1/users/@me/data-export/{dsar_request_id} (cancel)..."
        )
        cancel_start = time.time()
        resp = session.delete(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export/{dsar_request_id}",
            timeout=10,
        )
        duration = (time.time() - cancel_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "DELETE",
                "path": f"/api/v1/users/@me/data-export/{dsar_request_id}",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "dsar_cancel",
            }
        )
        if success:
            logger.info(f"DSAR cancel PASSED -> {resp.status_code}")
            self.ctx.test_dsar_id = None
        else:
            logger.warning(f"DSAR cancel -> {resp.status_code}: {resp.text[:200]}")

        # Step 5: admin lifecycle on a second request (detail -> approve
        # -> generate). Uses its own row because approve/generate move the
        # first request out of the cancellable states.
        logger.info("Testing DSAR admin lifecycle (second request)...")
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export",
            json={"password": test_pass, "format": "json"},
            timeout=10,
        )
        if 200 <= resp.status_code < 300:
            admin_id = None
            try:
                import src.api as _api3

                _db3 = _api3.get_db()
                if _db3:
                    _row3 = _db3.fetch_one(
                        "SELECT id FROM dsar_requests WHERE user_id = ? "
                        "AND status = 'pending' ORDER BY requested_at DESC LIMIT 1",
                        (self.ctx.test_user_id,),
                    )
                    if _row3:
                        admin_id = int(
                            _row3["id"] if isinstance(_row3, dict) else _row3[0]
                        )
            except Exception:
                pass
            if admin_id:
                base = self.ctx.base_url
                for method, path, label in [
                    (
                        "GET",
                        f"/api/v1/admin/data-export/{admin_id}",
                        "dsar_admin_get",
                    ),
                    (
                        "POST",
                        f"/api/v1/admin/data-export/{admin_id}/approve",
                        "dsar_admin_approve",
                    ),
                    (
                        "POST",
                        f"/api/v1/admin/data-export/{admin_id}/generate",
                        "dsar_admin_generate",
                    ),
                ]:
                    try:
                        if method == "GET":
                            r = session.get(f"{base}{path}", timeout=15)
                        else:
                            r = session.post(f"{base}{path}", json={}, timeout=60)
                        ok = 200 <= r.status_code < 300
                        self.ctx.results.append(
                            {
                                "method": method,
                                "path": path,
                                "status_code": r.status_code,
                                "duration_ms": 0,
                                "success": ok,
                                "label": label,
                            }
                        )
                        if ok:
                            logger.info(f"DSAR {label} PASSED -> {r.status_code}")
                        else:
                            logger.warning(
                                f"DSAR {label} -> {r.status_code}: {r.text[:200]}"
                            )
                    except Exception as e:
                        logger.warning(f"DSAR {label} exception: {e}")
                        self.ctx.results.append(
                            {
                                "method": method,
                                "path": path,
                                "status_code": 0,
                                "duration_ms": 0,
                                "success": False,
                                "label": label,
                                "error": str(e)[:200],
                            }
                        )

        # Step 6: deny flow on a third request (deny requires pending, so
        # it needs its own row). The denied row is terminal by design.
        logger.info("Testing DSAR deny flow (second request)...")
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export",
            json={"password": test_pass, "format": "json"},
            timeout=10,
        )
        if 200 <= resp.status_code < 300:
            deny_id = None
            try:
                import src.api as _api2

                _db2 = _api2.get_db()
                if _db2:
                    _row2 = _db2.fetch_one(
                        "SELECT id FROM dsar_requests WHERE user_id = ? "
                        "AND status = 'pending' ORDER BY requested_at DESC LIMIT 1",
                        (self.ctx.test_user_id,),
                    )
                    if _row2:
                        deny_id = int(
                            _row2["id"] if isinstance(_row2, dict) else _row2[0]
                        )
            except Exception:
                pass
            if deny_id:
                deny_resp = session.post(
                    f"{self.ctx.base_url}/api/v1/admin/data-export/{deny_id}/deny",
                    json={"reason": "self-test deny flow"},
                    timeout=15,
                )
                deny_ok = 200 <= deny_resp.status_code < 300
                self.ctx.results.append(
                    {
                        "method": "POST",
                        "path": f"/api/v1/admin/data-export/{deny_id}/deny",
                        "status_code": deny_resp.status_code,
                        "duration_ms": 0,
                        "success": deny_ok,
                        "label": "dsar_admin_deny",
                    }
                )
                if deny_ok:
                    logger.info("DSAR admin deny PASSED")
                else:
                    logger.warning(
                        f"DSAR admin deny -> {deny_resp.status_code}: "
                        f"{deny_resp.text[:200]}"
                    )
