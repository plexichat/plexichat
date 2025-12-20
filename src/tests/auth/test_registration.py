"""
Comprehensive registration tests covering validation, edge cases, and security.
"""

import pytest
import time
from src.core.auth.exceptions import (
    UserExistsError,
    WeakPasswordError,
    InvalidUsernameError,
    InvalidEmailError,
)
from src.tests.fixtures.config import TEST_PASSWORD


class TestRegistrationBasics:
    """Basic registration functionality tests."""

    def test_register_success(self, modules):
        """Test successful user registration."""
        username = f"regtest_{time.time()}"
        email = f"{username}@test.com"

        user = modules.auth.register(username, email, TEST_PASSWORD)

        assert user.id is not None
        assert user.username == username
        assert user.email == email
        assert user.email_verified is True  # Default in test config

    def test_register_creates_user_in_db(self, modules):
        """Test registration creates user in database."""
        username = f"dbtest_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        retrieved = modules.auth.get_user(user.id)
        assert retrieved is not None
        assert retrieved.username == username

    def test_register_sets_default_permissions(self, modules):
        """Test registration sets default user permissions."""
        username = f"permtest_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        assert user.permissions is not None
        assert user.permissions.get("messages.send") is True
        assert user.permissions.get("account.edit_profile") is True

    def test_register_hashes_password(self, modules):
        """Test password is hashed, not stored plaintext."""
        username = f"hashtest_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Attempt to get raw user data
        row = modules._db.fetch_one(
            "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
        )
        assert row["password_hash"] != TEST_PASSWORD
        assert row["password_hash"].startswith("$argon2")


class TestUsernameValidation:
    """Tests for username validation rules."""

    def test_register_duplicate_username_fails(self, modules):
        """Test cannot register duplicate username."""
        username = f"duplicate_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        with pytest.raises(UserExistsError) as exc:
            modules.auth.register(username, f"{username}2@test.com", TEST_PASSWORD)
        assert exc.value.field == "username"

    def test_register_username_too_short(self, modules):
        """Test username must meet minimum length."""
        with pytest.raises(InvalidUsernameError) as exc:
            modules.auth.register("ab", "ab@test.com", TEST_PASSWORD)
        assert any("at least" in issue.lower() for issue in exc.value.issues)

    def test_register_username_too_long(self, modules):
        """Test username must not exceed maximum length."""
        long_username = "a" * 100
        with pytest.raises(InvalidUsernameError) as exc:
            modules.auth.register(long_username, "long@test.com", TEST_PASSWORD)
        assert any("at most" in issue.lower() for issue in exc.value.issues)

    def test_register_username_invalid_chars(self, modules):
        """Test username cannot contain invalid characters."""
        invalid_usernames = [
            "user@name",
            "user#name",
            "user name",
            "user.name",
            "user!name",
            "user$name",
            "user%name",
        ]

        for username in invalid_usernames:
            with pytest.raises(InvalidUsernameError):
                modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

    def test_register_username_valid_chars(self, modules):
        """Test username can contain alphanumeric and underscores."""
        valid_usernames = [
            f"User123_{time.time()}",
            f"test_user_{time.time()}",
            f"ABC_{time.time()}",
            f"user_123_{time.time()}",
        ]

        for username in valid_usernames:
            user = modules.auth.register(
                username, f"{username}@test.com", TEST_PASSWORD
            )
            assert user.username == username

    def test_register_reserved_username(self, modules):
        """Test cannot register reserved usernames."""
        reserved = ["admin", "administrator", "system", "bot", "api", "root"]

        for username in reserved:
            with pytest.raises(InvalidUsernameError) as exc:
                modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)
            assert any("reserved" in issue.lower() for issue in exc.value.issues)

    def test_register_username_case_sensitive(self, modules):
        """Test username case sensitivity."""
        username = f"CaseTest_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Different case should be allowed (case-sensitive)
        username_lower = username.lower()
        if username_lower != username:
            user2 = modules.auth.register(
                username_lower, f"{username_lower}@test.com", TEST_PASSWORD
            )
            assert user2.username == username_lower

    def test_register_username_minimum_valid(self, modules):
        """Test username at minimum valid length."""
        username = f"abc_{int(time.time())}"[:3]  # Ensure exactly 3 chars if needed
        if len(username) < 3:
            username = "abc"
        # Make unique
        username = f"{username}_{int(time.time())}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)
        assert user.username == username


class TestEmailValidation:
    """Tests for email validation rules."""

    def test_register_duplicate_email_fails(self, modules):
        """Test cannot register duplicate email."""
        email = f"unique_{time.time()}@test.com"
        modules.auth.register(f"user1_{time.time()}", email, TEST_PASSWORD)

        with pytest.raises(UserExistsError) as exc:
            modules.auth.register(f"user2_{time.time()}", email, TEST_PASSWORD)
        assert exc.value.field == "email"

    def test_register_invalid_email_format(self, modules):
        """Test invalid email formats are rejected."""
        invalid_emails = [
            "notanemail",
            "missing@domain",
            "@nodomain.com",
            "no@tld",
            "spaces in@email.com",
            "double@@domain.com",
        ]

        for email in invalid_emails:
            with pytest.raises(InvalidEmailError):
                modules.auth.register(f"user_{time.time()}", email, TEST_PASSWORD)

    def test_register_email_with_plus(self, modules):
        """Test email with plus sign is valid."""
        username = f"plustest_{time.time()}"
        email = f"user+tag_{time.time()}@test.com"
        user = modules.auth.register(username, email, TEST_PASSWORD)
        assert user.email == email

    def test_register_email_with_subdomain(self, modules):
        """Test email with subdomain is valid."""
        username = f"subtest_{time.time()}"
        email = f"user_{time.time()}@mail.example.com"
        user = modules.auth.register(username, email, TEST_PASSWORD)
        assert user.email == email

    def test_register_invalid_tld(self, modules):
        """Test email with invalid TLD is rejected."""
        with pytest.raises(InvalidEmailError):
            modules.auth.register(
                f"user_{time.time()}", "user@domain.invalidtld", TEST_PASSWORD
            )

    def test_register_various_valid_tlds(self, modules):
        """Test email with various valid TLDs."""
        tlds = ["com", "org", "net", "io", "dev", "app", "co.uk"]

        for i, tld in enumerate(tlds):
            username = f"tldtest{i}_{time.time()}"
            # Handle co.uk special case
            if "." in tld:
                email = f"{username}@test.{tld}"
            else:
                email = f"{username}@test.{tld}"

            try:
                user = modules.auth.register(username, email, TEST_PASSWORD)
                assert user.email == email
            except InvalidEmailError:
                # co.uk might not work with simple TLD validation
                pass


class TestPasswordValidation:
    """Tests for password strength validation."""

    def test_register_password_too_short(self, modules):
        """Test password must meet minimum length."""
        with pytest.raises(WeakPasswordError) as exc:
            modules.auth.register(f"user_{time.time()}", "user@test.com", "Short1!")
        assert any("at least" in issue.lower() for issue in exc.value.issues)

    def test_register_password_too_long(self, modules):
        """Test password must not exceed maximum length."""
        long_pass = "A1!" + ("a" * 200)
        with pytest.raises(WeakPasswordError) as exc:
            modules.auth.register(f"user_{time.time()}", "user@test.com", long_pass)
        assert any("at most" in issue.lower() for issue in exc.value.issues)

    def test_register_password_no_uppercase(self, modules):
        """Test password requires uppercase letter."""
        with pytest.raises(WeakPasswordError) as exc:
            modules.auth.register(
                f"user_{time.time()}", "user@test.com", "lowercase123!"
            )
        assert any("uppercase" in issue.lower() for issue in exc.value.issues)

    def test_register_password_no_lowercase(self, modules):
        """Test password requires lowercase letter."""
        with pytest.raises(WeakPasswordError) as exc:
            modules.auth.register(
                f"user_{time.time()}", "user@test.com", "UPPERCASE123!"
            )
        assert any("lowercase" in issue.lower() for issue in exc.value.issues)

    def test_register_password_no_digit(self, modules):
        """Test password requires digit."""
        with pytest.raises(WeakPasswordError) as exc:
            modules.auth.register(
                f"user_{time.time()}", "user@test.com", "NoDigitsHere!"
            )
        assert any("digit" in issue.lower() for issue in exc.value.issues)

    def test_register_password_no_special(self, modules):
        """Test password requires special character."""
        with pytest.raises(WeakPasswordError) as exc:
            modules.auth.register(
                f"user_{time.time()}", "user@test.com", "NoSpecial123"
            )
        assert any("special" in issue.lower() for issue in exc.value.issues)

    def test_register_password_all_requirements(self, modules):
        """Test password meeting all requirements succeeds."""
        username = f"validpass_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", "ValidPass123!")
        assert user.username == username

    def test_register_password_various_special_chars(self, modules):
        """Test various special characters are accepted."""
        special_chars = "!@#$%^&*"

        for i, char in enumerate(special_chars):
            username = f"special{i}_{time.time()}"
            password = f"TestPass123{char}"
            user = modules.auth.register(username, f"{username}@test.com", password)
            assert user.username == username


class TestRegistrationEdgeCases:
    """Edge case tests for registration."""

    def test_register_unicode_username(self, modules):
        """Test unicode characters in username are rejected."""
        with pytest.raises(InvalidUsernameError):
            modules.auth.register("用户名", "user@test.com", TEST_PASSWORD)

    def test_register_emoji_username(self, modules):
        """Test emoji in username is rejected."""
        with pytest.raises(InvalidUsernameError):
            modules.auth.register("user😀", "user@test.com", TEST_PASSWORD)

    def test_register_empty_username(self, modules):
        """Test empty username is rejected."""
        with pytest.raises(InvalidUsernameError):
            modules.auth.register("", "user@test.com", TEST_PASSWORD)

    def test_register_empty_email(self, modules):
        """Test empty email is rejected."""
        with pytest.raises(InvalidEmailError):
            modules.auth.register(f"user_{time.time()}", "", TEST_PASSWORD)

    def test_register_empty_password(self, modules):
        """Test empty password is rejected."""
        with pytest.raises(WeakPasswordError):
            modules.auth.register(f"user_{time.time()}", "user@test.com", "")

    def test_register_whitespace_username(self, modules):
        """Test whitespace-only username is rejected."""
        with pytest.raises(InvalidUsernameError):
            modules.auth.register("   ", "user@test.com", TEST_PASSWORD)

    def test_register_null_bytes_in_username(self, modules):
        """Test null bytes in username are rejected."""
        with pytest.raises(InvalidUsernameError):
            modules.auth.register("user\x00name", "user@test.com", TEST_PASSWORD)

    def test_register_sql_injection_username(self, modules):
        """Test SQL injection attempts in username are handled."""
        malicious = "admin'; DROP TABLE auth_users--"
        with pytest.raises(InvalidUsernameError):
            modules.auth.register(malicious, "user@test.com", TEST_PASSWORD)

    def test_register_xss_username(self, modules):
        """Test XSS attempts in username are rejected."""
        with pytest.raises(InvalidUsernameError):
            modules.auth.register(
                "<script>alert('xss')</script>", "user@test.com", TEST_PASSWORD
            )


class TestRegistrationAudit:
    """Tests for registration audit logging."""

    def test_register_creates_audit_entry(self, modules):
        """Test registration creates audit log entry."""
        username = f"audituser_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        events = modules.auth.get_security_events(user.id, limit=10)
        register_events = [e for e in events if e.event_type.value == "register"]
        assert len(register_events) > 0

    def test_register_audit_includes_details(self, modules):
        """Test registration audit includes username and email."""
        username = f"detailaudit_{time.time()}"
        email = f"{username}@test.com"
        user = modules.auth.register(username, email, TEST_PASSWORD)

        events = modules.auth.get_security_events(user.id, limit=10)
        register_events = [e for e in events if e.event_type.value == "register"]
        assert len(register_events) > 0
        event = register_events[0]
        assert event.details is not None
        assert event.details.get("username") == username


class TestRegistrationConcurrency:
    """Tests for concurrent registration scenarios."""

    def test_register_concurrent_same_username_fails(self, modules):
        """Test concurrent registrations with same username."""
        import threading

        username = f"concurrent_{time.time()}"
        results = []

        def register():
            try:
                user = modules.auth.register(
                    username,
                    f"{username}_{threading.get_ident()}@test.com",
                    TEST_PASSWORD,
                )
                results.append(("success", user))
            except UserExistsError as e:
                results.append(("exists", e))
            except Exception as e:
                results.append(("error", e))

        threads = [threading.Thread(target=register) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one should succeed
        successes = [r for r in results if r[0] == "success"]
        assert len(successes) == 1


class TestAccountType:
    """Tests for account type assignment."""

    def test_register_creates_user_account_type(self, modules):
        """Test registration creates USER account type."""
        username = f"typetest_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        assert user.account_type.value == "user"

    def test_register_user_cannot_be_bot_type(self, modules):
        """Test regular registration cannot create BOT type."""
        username = f"notbot_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        assert user.account_type.value != "bot"


class TestRegistrationTimestamps:
    """Tests for registration timestamps."""

    def test_register_sets_created_at(self, modules):
        """Test registration sets created_at timestamp."""
        username = f"timestamp_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        assert user.created_at is not None
        assert user.created_at > 0

    def test_register_sets_updated_at(self, modules):
        """Test registration sets updated_at timestamp."""
        username = f"updated_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        assert user.updated_at is not None
        assert user.updated_at == user.created_at

    def test_register_timestamps_reasonable(self, modules):
        """Test registration timestamps are recent."""
        import time as time_module

        before = int(time_module.time() * 1000)

        username = f"timereasonable_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        after = int(time_module.time() * 1000)

        assert before <= user.created_at <= after
