"""Two-Factor Authentication endpoint tester mixin.

Tests user and admin 2FA flows in standalone mode:
  enable -> confirm -> status -> disable
"""

import time
import hashlib

import utils.logger as logger

try:
    import pyotp  # type: ignore
except ImportError:
    pyotp = None

from .base import EndpointTesterBase


class TwoFAMixin(EndpointTesterBase):
    """Tests 2FA-related API endpoints."""

    def _get_current_totp_code(self, secret: str) -> str:
        if pyotp:
            return pyotp.TOTP(secret).now()
        # Fallback: compute a TOTP code manually (RFC 6238)
        import hmac
        import struct
        import base64

        time_step = 30
        now = int(time.time())
        counter = now // time_step
        key = base64.b32decode(secret, casefold=True)
        msg = struct.pack(">Q", counter)
        mac = hmac.new(key, msg, hashlib.sha1).digest()
        offset = mac[-1] & 0x0F
        code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % 1000000
        return f"{code:06d}"

    def test_user_2fa_flow(self) -> None:
        """Test user 2FA: enable -> confirm -> status -> disable."""
        if not self.ctx.standalone_mode:
            return

        password = self.ctx._test_password
        if not password:
            logger.debug("Skipping user 2FA flow (no test password)")
            return

        session = self.ctx.session

        # Step 1: Enable 2FA
        logger.info("Testing POST /api/v1/auth/2fa/enable...")
        enable_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/auth/2fa/enable",
            json={"password": password},
            timeout=5,
        )
        duration = (time.time() - enable_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/auth/2fa/enable",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "2fa_enable",
            }
        )
        if not success:
            logger.warning(f"2FA enable -> {resp.status_code}: {resp.text[:200]}")
            return
        logger.info(f"2FA enable PASSED -> {resp.status_code}")
        try:
            resp_data = resp.json()
            totp_secret = resp_data.get("secret")
            backup_codes = resp_data.get("backup_codes", [])
            if not totp_secret:
                logger.warning("2FA enable succeeded but no secret returned")
                return
            logger.info(
                f"TOTP secret received ({len(totp_secret)} chars), {len(backup_codes)} backup codes"
            )
        except Exception as e:
            logger.warning(f"Could not parse 2FA enable response: {e}")
            return

        time.sleep(0.1)

        # Step 2: Confirm 2FA with valid TOTP code
        totp_code = self._get_current_totp_code(totp_secret)
        logger.info(
            f"Testing POST /api/v1/auth/2fa/confirm with code (len={len(totp_code)})..."
        )
        confirm_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/auth/2fa/confirm",
            json={"code": totp_code},
            timeout=5,
        )
        duration = (time.time() - confirm_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/auth/2fa/confirm",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "2fa_confirm",
            }
        )
        if success:
            logger.info(f"2FA confirm PASSED -> {resp.status_code}")
        else:
            logger.warning(f"2FA confirm -> {resp.status_code}: {resp.text[:200]}")
            return

        time.sleep(0.1)

        # Step 3: Check 2FA status
        logger.info("Testing GET /api/v1/auth/2fa/status...")
        status_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/auth/2fa/status",
            timeout=5,
        )
        duration = (time.time() - status_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": "/api/v1/auth/2fa/status",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "2fa_status",
            }
        )
        if success:
            logger.info(f"2FA status PASSED -> {resp.status_code}")
        else:
            logger.warning(f"2FA status -> {resp.status_code}: {resp.text[:200]}")

        time.sleep(0.1)

        # Step 4: Disable 2FA
        new_code = self._get_current_totp_code(totp_secret)
        logger.info("Testing POST /api/v1/auth/2fa/disable...")
        disable_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/auth/2fa/disable",
            json={"password": password, "code": new_code},
            timeout=5,
        )
        duration = (time.time() - disable_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/auth/2fa/disable",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "2fa_disable",
            }
        )
        if success:
            logger.info(f"2FA disable PASSED -> {resp.status_code}")
        else:
            logger.warning(f"2FA disable -> {resp.status_code}: {resp.text[:200]}")

    def test_admin_2fa_flow(self) -> None:
        """Test admin 2FA: begin-setup -> verify-otp -> disable -> regenerate."""
        if not self.ctx.standalone_mode:
            return

        password = self.ctx._test_password
        if not password:
            logger.debug("Skipping admin 2FA flow (no test password)")
            return

        session = self.ctx.session

        # Step 1: Begin OTP setup
        logger.info("Testing POST /api/v1/admin/auth/2fa/begin-setup...")
        begin_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/admin/auth/2fa/begin-setup",
            json={"current_password": password},
            timeout=5,
        )
        duration = (time.time() - begin_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/admin/auth/2fa/begin-setup",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "admin_2fa_begin_setup",
            }
        )
        if not success:
            logger.warning(
                f"Admin 2FA begin-setup -> {resp.status_code}: {resp.text[:200]}"
            )
            return
        logger.info(f"Admin 2FA begin-setup PASSED -> {resp.status_code}")

        try:
            resp_data = resp.json()
            otp_secret = resp_data.get("otp_secret")
            challenge_token = resp_data.get("challenge_token")
            admin_id = resp_data.get("admin_id")
            if not otp_secret:
                logger.warning(
                    "Admin 2FA begin-setup succeeded but no otp_secret returned"
                )
                return
            logger.info(f"Admin OTP secret received ({len(otp_secret)} chars)")
        except Exception as e:
            logger.warning(f"Could not parse admin 2FA begin-setup response: {e}")
            return

        time.sleep(0.1)

        # Step 2: Verify OTP
        otp_code = self._get_current_totp_code(otp_secret)
        logger.info("Testing POST /api/v1/admin/verify-otp...")
        verify_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/admin/verify-otp",
            json={
                "code": otp_code,
                "challenge_token": challenge_token or "",
                "admin_id": str(admin_id),
                "is_setup": True,
            },
            timeout=5,
        )
        duration = (time.time() - verify_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/admin/verify-otp",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "admin_verify_otp",
            }
        )
        if success:
            logger.info(f"Admin verify-otp PASSED -> {resp.status_code}")
        else:
            logger.warning(f"Admin verify-otp -> {resp.status_code}: {resp.text[:200]}")

        time.sleep(0.1)

        # Step 3: Regenerate backup codes while OTP is active
        logger.info("Testing POST /api/v1/admin/auth/2fa/regenerate-backup-codes...")
        regen_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/admin/auth/2fa/regenerate-backup-codes",
            json={"current_password": password},
            timeout=5,
        )
        duration = (time.time() - regen_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/admin/auth/2fa/regenerate-backup-codes",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "admin_2fa_regenerate",
            }
        )
        if success:
            logger.info(f"Admin 2FA regenerate PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Admin 2FA regenerate -> {resp.status_code}: {resp.text[:200]}"
            )

        time.sleep(0.1)

        # Step 4: Disable OTP
        final_code = self._get_current_totp_code(otp_secret)
        logger.info("Testing POST /api/v1/admin/auth/2fa/disable...")
        disable_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/admin/auth/2fa/disable",
            json={"current_password": password, "code": final_code},
            timeout=5,
        )
        duration = (time.time() - disable_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/admin/auth/2fa/disable",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "admin_2fa_disable",
            }
        )
        if success:
            logger.info(f"Admin 2FA disable PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Admin 2FA disable -> {resp.status_code}: {resp.text[:200]}"
            )
