import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI
import sys
import os

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from src.api.routes.auth import router
from src.core.auth.exceptions import TokenInvalidError, WeakPasswordError
import utils.config as config

# Setup config before importing anything that uses it
# Use a non-existent path to trigger default config logic
config.setup(config_path="non_existent.yaml", default_config={
    "rate_limiting": {
        "routes": {
            "send_message": {"requests": 5}
        }
    },
    "authentication": {
        "password": {
            "min_length": 12,
            "max_length": 128,
            "require_uppercase": True,
            "require_lowercase": True,
            "require_digit": True,
            "require_special": True,
        }
    }
})

class TestPasswordResetAPI(unittest.TestCase):
    def setUp(self):
        self.auth_mock = MagicMock()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1/auth")
        
        # Patch api.get_auth
        self.patcher = patch("src.api.get_auth", return_value=self.auth_mock)
        self.patcher.start()
        
        self.client = TestClient(self.app)

    def tearDown(self):
        self.patcher.stop()

    def test_request_password_reset_api(self):
        """Test the request password reset API endpoint."""
        email = "test@example.com"
        
        response = self.client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": email}
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        self.auth_mock.request_password_reset.assert_called_once_with(email)

    def test_confirm_password_reset_api_success(self):
        """Test the confirm password reset API endpoint - success case."""
        token = "email.123.secret"
        new_password = "NewSecurePassword123!"
        self.auth_mock.reset_password.return_value = True
        
        response = self.client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": token, "new_password": new_password}
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})
        self.auth_mock.reset_password.assert_called_once_with(token, new_password)

    def test_confirm_password_reset_api_invalid_token(self):
        """Test the confirm password reset API endpoint - invalid token."""
        token = "invalid_token"
        new_password = "NewSecurePassword123!"
        self.auth_mock.reset_password.side_effect = TokenInvalidError("Invalid token")
        
        response = self.client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": token, "new_password": new_password}
        )
        
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid token", response.json()["detail"]["error"]["message"])

    def test_confirm_password_reset_api_weak_password(self):
        """Test the confirm password reset API endpoint - weak password."""
        token = "email.123.secret"
        new_password = "weak"
        self.auth_mock.reset_password.side_effect = WeakPasswordError("Weak password", ["Too short"])
        
        response = self.client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": token, "new_password": new_password}
        )
        
        self.assertEqual(response.status_code, 400)
        self.assertIn("Weak password", response.json()["detail"]["error"]["message"])

if __name__ == "__main__":
    unittest.main()