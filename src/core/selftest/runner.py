"""
Self-Test Runner - Automated API endpoint validation.

Discovers all registered routes from FastAPI and exercises them.
Supports automated authentication, retry logic, and traceback capture.
"""

import time
import json
import traceback
from typing import List, Dict, Any, Optional, Set
import requests
import websocket

import src.api as api
import utils.config as config
import utils.logger as logger

class SelfTestRunner:
    """Automated API test runner."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        # Always reload config from utility to ensure latest values
        self.config = config.get("selftest", {})
        self.token: Optional[str] = None
        self.test_user_id: Optional[int] = None
        self.results: List[Dict[str, Any]] = []
        self.start_time = 0.0
        self.session = requests.Session()

    def run_all(self) -> bool:
        """Run all discovered API tests."""
        self.start_time = time.time()
        logger.info("=" * 60)
        logger.info("STARTING API SELF-TEST SUITE")
        logger.info(f"Target: {self.base_url}")
        logger.info("=" * 60)
        
        # 1. Setup Auth
        if not self._setup_authentication():
            logger.error("Authentication setup failed. Aborting tests.")
            return False

        # 2. Discover Routes
        routes = self._discover_routes()
        logger.info(f"Discovered {len(routes)} endpoints")

        # 3. WebSocket Test
        self._test_websocket()

        # 4. Execute API Tests
        excluded = set(self.config.get("excluded_endpoints", []))
        
        logger.info("Executing API tests...")
        
        for route in routes:
            path = route["path"]
            method = route["method"]
            
            if path in excluded or f"{method}:{path}" in excluded:
                logger.debug(f"Skipping excluded endpoint: {method} {path}")
                continue
                
            self._test_endpoint(method, path, route)
            # Very small delay to allow async tasks to settle
            time.sleep(0.005)

        # 5. Summary
        success = self._report_summary()
        
        # 6. Cleanup (Delete test user)
        self._cleanup_test_data()
        
        return success

    def _setup_authentication(self) -> bool:
        """Create a temporary test user and get a session token."""
        user_config = self.config.get("test_user", {})
        username = user_config.get("username", "selftest_admin")
        password = user_config.get("password", "SelfTest_Password_123!")
        email = user_config.get("email", "selftest@plexichat.com")

        logger.info(f"Setting up test user: {username}")
        
        auth_mod = api.get_auth()
        if not auth_mod:
            logger.error("Auth module not available for self-test")
            return False

        try:
            # Check if user exists
            user = auth_mod.get_user_by_username(username)
            if not user:
                logger.info(f"Creating new test user: {username}")
                user = auth_mod.register(username, email, password)
                # Grant admin permission
                auth_mod.grant_permission(user.id, "admin.*")
                auth_mod.grant_permission(user.id, "*")
            
            self.test_user_id = user.id
            
            # Login via API to get token
            resp = self.session.post(
                f"{self.base_url}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=10
            )
            
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("token")
                if self.token:
                    # Update session with token and self-test headers
                    self.session.headers.update({
                        "Authorization": f"Bearer {self.token}",
                        "X-Plexichat-SelfTest": "true"
                    })
                    logger.info("Test authentication successful")
                    return True
            
            logger.error(f"Login failed with status {resp.status_code}: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"Auth setup error: {e}")
            return False

    def _cleanup_test_data(self) -> None:
        """Remove the test user and their data."""
        if not self.test_user_id:
            return
            
        logger.info("Cleaning up test data...")
        auth_mod = api.get_auth()
        db = api.get_db()
        
        if auth_mod and db:
            try:
                db.begin_transaction()
                db.execute("DELETE FROM auth_users WHERE id = ?", (self.test_user_id,))
                db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (self.test_user_id,))
                db.execute("DELETE FROM auth_audit_log WHERE user_id = ?", (self.test_user_id,))
                db.execute("DELETE FROM pres_presence WHERE user_id = ?", (self.test_user_id,))
                db.execute("DELETE FROM rel_friends WHERE user_id = ? OR friend_id = ?", (self.test_user_id, self.test_user_id))
                db.commit()
                logger.info(f"Deleted self-test user {self.test_user_id}")
            except Exception as e:
                db.rollback()
                logger.error(f"Cleanup failed: {e}")

    def _test_websocket(self) -> None:
        """Test WebSocket gateway connectivity."""
        ws_url = self.base_url.replace("http", "ws") + "/gateway"
        logger.info(f"Testing WebSocket gateway: {ws_url}")
        
        start = time.time()
        try:
            ws_conn = websocket.create_connection(ws_url, timeout=5)
            
            # 1. Receive HELLO
            hello = json.loads(ws_conn.recv())
            if hello.get("op") == 10: # HELLO
                # 2. Identify
                ws_conn.send(json.dumps({
                    "op": 2, # IDENTIFY
                    "d": {
                        "token": self.token,
                        "intents": 0,
                        "properties": {"os": "selftest", "browser": "python", "device": "selftest"}
                    }
                }))
                
                # 3. Receive READY
                ready = json.loads(ws_conn.recv())
                if ready.get("t") == "READY":
                    duration = (time.time() - start) * 1000
                    logger.info(f"WebSocket connected successfully ({duration:.1f}ms)")
                    ws_conn.close()
                    return
            
            logger.error("WebSocket identify failed")
            ws_conn.close()
        except Exception as e:
            logger.error(f"WebSocket test error: {e}")

    def _discover_routes(self) -> List[Dict[str, Any]]:
        """Extract all routes from the running FastAPI app via openapi.json."""
        try:
            resp = self.session.get(f"{self.base_url}/openapi.json", timeout=10)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch OpenAPI spec: {resp.status_code}")
                return []
                
            openapi = resp.json()
            routes = []
            
            for path, methods in openapi.get("paths", {}).items():
                for method, details in methods.items():
                    if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                        routes.append({
                            "path": path,
                            "method": method.upper(),
                            "summary": details.get("summary", ""),
                            "operation_id": details.get("operationId", ""),
                            "parameters": details.get("parameters", []),
                            "request_body": details.get("requestBody", {})
                        })
            return sorted(routes, key=lambda x: x["path"])
        except Exception as e:
            logger.error(f"Route discovery failed: {e}")
            return []

    def _test_endpoint(self, method: str, path: str, route_details: Dict[str, Any]) -> None:
        """Test a specific endpoint."""
        url_path = path
        for param in route_details.get("parameters", []):
            if param.get("in") == "path":
                name = param.get("name")
                val = "1" 
                if "user" in name: val = str(self.test_user_id)
                url_path = url_path.replace(f"{{{name}}}", val)

        start = time.time()
        try:
            resp = self.session.request(method, f"{self.base_url}{url_path}", timeout=5)
            duration = (time.time() - start) * 1000
            
            success = resp.status_code < 500
            
            # If failed (500) and retry enabled, try once more with debug headers
            traceback_data = None
            if not success and self.config.get("retry_on_failure", True):
                logger.warning(f"Retrying failed endpoint with debug: {method} {path}")
                # Create a fresh request for the retry
                retry_resp = requests.request(
                    method, 
                    f"{self.base_url}{url_path}", 
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "X-Plexichat-SelfTest": "true",
                        "X-Plexichat-SelfTest-Debug": "true"
                    },
                    timeout=10
                )
                if retry_resp.status_code >= 400:
                    try:
                        error_data = retry_resp.json()
                        traceback_data = error_data.get("error", {}).get("traceback")
                    except Exception:
                        pass

            result = {
                "method": method,
                "path": path,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "traceback": traceback_data
            }
            
            self.results.append(result)
            
            if not success:
                logger.error(f"FAILED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)")
                if traceback_data:
                    logger.error(f"Captured Traceback for {path}:\n{traceback_data}")
            elif self.config.get("verbose", False):
                # Only log successful tests if verbose is enabled
                logger.info(f"PASSED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)")
                
        except Exception as e:
            self.results.append({
                "method": method,
                "path": path,
                "status_code": 0,
                "duration_ms": 0,
                "success": False,
                "error": str(e)
            })
            logger.error(f"EXCEPTION: {method:<6} {path:<40} -> {e}")

    def _report_summary(self) -> bool:
        """Log the test summary."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r["success"])
        failed = total - passed
        duration = time.time() - self.start_time
        
        logger.info("=" * 60)
        logger.info("SELF-TEST SUMMARY")
        logger.info(f"Total Endpoints: {total}")
        logger.info(f"Passed:          {passed}")
        logger.info(f"Failed:          {failed}")
        logger.info(f"Success Rate:    {(passed/total*100 if total > 0 else 0):.1f}%")
        logger.info(f"Total Duration:  {duration:.2f}s")
        logger.info("=" * 60)
        
        if failed > 0:
            logger.error("Failed Endpoints (500 Internal Errors):")
            for r in self.results:
                if not r["success"]:
                    logger.error(f"  - {r['method']:<6} {r['path']} (Status: {r['status_code']})")
            logger.error("See detailed logs and tracebacks above or in app.log")
            
        return failed == 0