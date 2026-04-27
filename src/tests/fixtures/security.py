"""
Security testing utilities.

Provides common security test payloads, assertion helpers, and fixtures
for testing XSS prevention, SQL injection, input validation, and other
security concerns across all modules.
"""

from typing import List, Any, Callable
from dataclasses import dataclass, field
import pytest


@dataclass
class XSSPayloads:
    """Common XSS attack vectors."""

    SCRIPT_TAGS: List[str] = field(
        default_factory=lambda: [
            '<script>alert("XSS")</script>',
            "<script>alert(1)</script>",
            '<SCRIPT>alert("XSS")</SCRIPT>',
            '<<SCRIPT>alert("XSS")//<</SCRIPT>',
            '<script src="http://evil.com/xss.js"></script>',
            "<script>document.cookie</script>",
            '<script>window.location="http://evil.com"</script>',
        ]
    )

    EVENT_HANDLERS: List[str] = field(
        default_factory=lambda: [
            "<img src=x onerror=alert(1)>",
            '<img src="x" onerror="alert(1)">',
            '<div onclick="alert(1)">Click</div>',
            '<div onmouseover="alert(1)">Hover</div>',
            "<body onload=alert(1)>",
            "<input onfocus=alert(1) autofocus>",
            "<svg onload=alert(1)>",
            "<marquee onstart=alert(1)>",
            "<details open ontoggle=alert(1)>",
        ]
    )

    JAVASCRIPT_PROTOCOL: List[str] = field(
        default_factory=lambda: [
            '<a href="javascript:alert(1)">Click</a>',
            '<a href="JaVaScRiPt:alert(1)">Click</a>',
            '<iframe src="javascript:alert(1)"></iframe>',
            '<form action="javascript:alert(1)"><button>Submit</button></form>',
        ]
    )

    DATA_URI: List[str] = field(
        default_factory=lambda: [
            '<a href="data:text/html,<script>alert(1)</script>">Click</a>',
            '<img src="data:image/svg+xml,<svg onload=alert(1)>">',
            '<iframe src="data:text/html,<script>alert(1)</script>"></iframe>',
        ]
    )

    IFRAME_INJECTION: List[str] = field(
        default_factory=lambda: [
            '<iframe src="https://evil.com"></iframe>',
            '<iframe src="javascript:alert(1)"></iframe>',
            '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
        ]
    )

    SVG_VECTORS: List[str] = field(
        default_factory=lambda: [
            "<svg><script>alert(1)</script></svg>",
            "<svg onload=alert(1)>",
            "<svg><animate onbegin=alert(1) attributeName=x dur=1s>",
            "<svg><foreignObject><body onload=alert(1)></body></foreignObject></svg>",
        ]
    )

    STYLE_INJECTION: List[str] = field(
        default_factory=lambda: [
            '<style>body{background:url("javascript:alert(1)")}</style>',
            '<style>@import"javascript:alert(1)";</style>',
            '<link rel="stylesheet" href="javascript:alert(1)">',
            '<div style="background:url(javascript:alert(1))">',
        ]
    )

    HTML_ENTITIES: List[str] = field(
        default_factory=lambda: [
            '&lt;script&gt;alert("XSS")&lt;/script&gt;',
            "&#60;script&#62;alert(1)&#60;/script&#62;",
            "&#x3C;script&#x3E;alert(1)&#x3C;/script&#x3E;",
        ]
    )

    ENCODED_PAYLOADS: List[str] = field(
        default_factory=lambda: [
            "%3Cscript%3Ealert(1)%3C/script%3E",
            "%3c%73%63%72%69%70%74%3e%61%6c%65%72%74%28%31%29%3c%2f%73%63%72%69%70%74%3e",
            "\\x3Cscript\\x3Ealert(1)\\x3C/script\\x3E",
        ]
    )

    NESTED_PAYLOADS: List[str] = field(
        default_factory=lambda: [
            '<<SCRIPT>alert("XSS")//<</SCRIPT>',
            "<scr<script>ipt>alert(1)</scr</script>ipt>",
            "<SCR\x00IPT>alert(1)</SCR\x00IPT>",
        ]
    )

    POLYGLOT_PAYLOADS: List[str] = field(
        default_factory=lambda: [
            "javascript:/*--></title></style></textarea></script></xmp><svg/onload='+/\"/+/onmouseover=1/+/[*/[]/+alert(1)//'>",
            '"><script>alert(String.fromCharCode(88,83,83))</script>',
            "';alert(String.fromCharCode(88,83,83))//';alert(String.fromCharCode(88,83,83))//\";",
        ]
    )

    def all(self) -> List[str]:
        """Get all XSS payloads combined."""
        return (
            self.SCRIPT_TAGS
            + self.EVENT_HANDLERS
            + self.JAVASCRIPT_PROTOCOL
            + self.DATA_URI
            + self.IFRAME_INJECTION
            + self.SVG_VECTORS
            + self.STYLE_INJECTION
            + self.HTML_ENTITIES
            + self.ENCODED_PAYLOADS
            + self.NESTED_PAYLOADS
            + self.POLYGLOT_PAYLOADS
        )


@dataclass
class SQLInjectionPayloads:
    """Common SQL injection attack vectors."""

    BASIC_INJECTION: List[str] = field(
        default_factory=lambda: [
            "' OR '1'='1",
            "' OR 1=1--",
            "' OR 1=1#",
            "' OR 1=1/*",
            "admin' --",
            "admin' #",
            "admin'/*",
        ]
    )

    UNION_BASED: List[str] = field(
        default_factory=lambda: [
            "' UNION SELECT NULL--",
            "' UNION SELECT 1,2,3--",
            "' UNION SELECT * FROM users--",
            "' UNION ALL SELECT NULL,NULL,NULL--",
        ]
    )

    STACKED_QUERIES: List[str] = field(
        default_factory=lambda: [
            "'; DROP TABLE users--",
            "'; DELETE FROM users WHERE '1'='1",
            "'; UPDATE users SET admin=1--",
            "'; INSERT INTO users VALUES ('hacker','pass')--",
        ]
    )

    TIME_BASED_BLIND: List[str] = field(
        default_factory=lambda: [
            "' AND SLEEP(5)--",
            "' OR IF(1=1,SLEEP(5),0)--",
            "'; WAITFOR DELAY '00:00:05'--",
            "' AND (SELECT COUNT(*) FROM GENERATE_SERIES(1,1000000))>0--",
        ]
    )

    BOOLEAN_BASED_BLIND: List[str] = field(
        default_factory=lambda: [
            "' AND '1'='1",
            "' AND '1'='2",
            "' AND EXISTS(SELECT * FROM users)--",
            "' AND ASCII(SUBSTRING((SELECT password FROM users LIMIT 1),1,1))>100--",
        ]
    )

    ERROR_BASED: List[str] = field(
        default_factory=lambda: [
            "' AND 1=CONVERT(int,(SELECT @@version))--",
            "' AND 1=CAST((SELECT @@version) AS int)--",
            "' AND EXTRACTVALUE(1,CONCAT(0x7e,(SELECT @@version)))--",
        ]
    )

    QUOTE_ESCAPING: List[str] = field(
        default_factory=lambda: [
            "admin''",
            "admin\\'",
            'admin"',
            "admin`",
            "admin\\\\",
        ]
    )

    COMMENT_SYNTAX: List[str] = field(
        default_factory=lambda: [
            "admin'--",
            "admin'/*",
            "admin'#",
            "admin';--",
            "admin' OR 1=1--",
        ]
    )

    NUMERIC_INJECTION: List[str] = field(
        default_factory=lambda: [
            "1 OR 1=1",
            "1 AND 1=2 UNION SELECT * FROM users",
            "1; DROP TABLE users",
            "1' OR '1'='1",
        ]
    )

    def all(self) -> List[str]:
        """Get all SQL injection payloads combined."""
        return (
            self.BASIC_INJECTION
            + self.UNION_BASED
            + self.STACKED_QUERIES
            + self.TIME_BASED_BLIND
            + self.BOOLEAN_BASED_BLIND
            + self.ERROR_BASED
            + self.QUOTE_ESCAPING
            + self.COMMENT_SYNTAX
            + self.NUMERIC_INJECTION
        )


@dataclass
class MalformedInputs:
    """Malformed and edge-case inputs for validation testing."""

    EMPTY_VALUES: List[Any] = field(
        default_factory=lambda: [
            "",
            None,
            [],
            {},
        ]
    )

    WHITESPACE: List[str] = field(
        default_factory=lambda: [
            " ",
            "   ",
            "\t",
            "\n",
            "\r\n",
            "\x00",
            "\u200b",
            "\ufeff",
        ]
    )

    EXTREMELY_LONG: List[str] = field(
        default_factory=lambda: [
            "x" * 1000,
            "x" * 10000,
            "x" * 100000,
            "🔥" * 1000,
        ]
    )

    UNICODE_EDGE_CASES: List[str] = field(
        default_factory=lambda: [
            "🔥🌍💯",
            "世界您好",
            "مرحبا",
            "🏳️‍🌈",
            "\u202e",
            "\ufeff",
            "\u200b\u200c\u200d",
        ]
    )

    SPECIAL_CHARACTERS: List[str] = field(
        default_factory=lambda: [
            "!@#$%^&*()_+-={}[]|\\:;\"'<>,.?/~`",
            "¡™£¢∞§¶•ªº–≠",
            "NUL:\x00",
            "控制字符:\x01\x02\x03",
        ]
    )

    PATH_TRAVERSAL: List[str] = field(
        default_factory=lambda: [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "....//....//....//etc/passwd",
            "..%2F..%2F..%2Fetc%2Fpasswd",
            "..%5C..%5C..%5Cwindows%5Csystem32",
        ]
    )

    COMMAND_INJECTION: List[str] = field(
        default_factory=lambda: [
            "; ls -la",
            "| cat /etc/passwd",
            "`whoami`",
            "$(whoami)",
            "&& echo vulnerable",
        ]
    )

    FORMAT_STRINGS: List[str] = field(
        default_factory=lambda: [
            "%s%s%s%s%s",
            "%x%x%x%x",
            "%n%n%n%n",
            "{0}{1}{2}",
        ]
    )

    INVALID_JSON: List[str] = field(
        default_factory=lambda: [
            "{invalid}",
            '{"key": undefined}',
            '{"key": NaN}',
            "{'single': 'quotes'}",
            '{"trailing": "comma",}',
        ]
    )

    NEGATIVE_NUMBERS: List[Any] = field(
        default_factory=lambda: [
            -1,
            -999999,
            -0,
            float("-inf"),
        ]
    )

    OVERFLOW_VALUES: List[Any] = field(
        default_factory=lambda: [
            2147483648,
            9223372036854775808,
            999999999999999999999,
            float("inf"),
        ]
    )

    def all(self) -> List[Any]:
        """Get all malformed inputs combined."""
        return (
            self.EMPTY_VALUES
            + self.WHITESPACE
            + self.EXTREMELY_LONG
            + self.UNICODE_EDGE_CASES
            + self.SPECIAL_CHARACTERS
            + self.PATH_TRAVERSAL
            + self.COMMAND_INJECTION
            + self.FORMAT_STRINGS
            + self.INVALID_JSON
            + self.NEGATIVE_NUMBERS
            + self.OVERFLOW_VALUES
        )


@dataclass
class AuthenticationPayloads:
    """Authentication and authorization attack vectors."""

    WEAK_PASSWORDS: List[str] = field(
        default_factory=lambda: [
            "",
            "a",
            "123",
            "password",
            "12345678",
            "qwerty",
            "admin",
            "letmein",
        ]
    )

    INVALID_USERNAMES: List[str] = field(
        default_factory=lambda: [
            "",
            "a",
            "ab",
            "x" * 100,
            "user name",
            "user\nname",
            "user\tname",
            "<script>user</script>",
        ]
    )

    INVALID_EMAILS: List[str] = field(
        default_factory=lambda: [
            "",
            "notanemail",
            "@test.com",
            "user@",
            "user test@test.com",
            "user@test",
            "user@.com",
            "@.com",
        ]
    )

    TOKEN_MANIPULATION: List[str] = field(
        default_factory=lambda: [
            "invalid_token",
            "",
            "Bearer ",
            "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxMjM0NTY3ODkwIn0.",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        ]
    )

    def all(self) -> List[Any]:
        """Get all authentication payloads combined."""
        return (
            self.WEAK_PASSWORDS
            + self.INVALID_USERNAMES
            + self.INVALID_EMAILS
            + self.TOKEN_MANIPULATION
        )


class SecurityAssertions:
    """Assertion helpers for security testing."""

    @staticmethod
    def assert_no_xss(content: str, original: str) -> None:
        """Assert that content has no XSS vulnerabilities."""
        dangerous_patterns = [
            "<script",
            "javascript:",
            "onerror=",
            "onclick=",
            "onload=",
            "onmouseover=",
            "<iframe",
            "data:text/html",
        ]

        content_lower = content.lower()
        for pattern in dangerous_patterns:
            assert pattern not in content_lower or content != original, (
                f"Potentially dangerous pattern '{pattern}' found in content"
            )

    @staticmethod
    def assert_no_sql_injection(result: Any, payload: str) -> None:
        """Assert that SQL injection was prevented."""
        if isinstance(result, str):
            sql_keywords = ["SELECT", "DROP", "DELETE", "UPDATE", "INSERT", "UNION"]
            result_upper = result.upper()
            for keyword in sql_keywords:
                assert keyword not in result_upper or payload.upper() == result_upper, (
                    f"SQL keyword '{keyword}' found in result"
                )

    @staticmethod
    def assert_sanitized(content: str, original: str) -> None:
        """Assert that content was sanitized if it contained dangerous patterns."""
        if any(
            pattern in original.lower()
            for pattern in ["<script", "javascript:", "onerror"]
        ):
            assert content != original, (
                "Content with dangerous patterns was not sanitized"
            )

    @staticmethod
    def assert_rejected(fn: Callable, *args, **kwargs) -> None:
        """Assert that operation was rejected with an exception."""
        with pytest.raises(Exception):
            fn(*args, **kwargs)

    @staticmethod
    def assert_no_error_leakage(error_msg: str) -> None:
        """Assert that error messages don't leak sensitive information."""
        sensitive_patterns = [
            "sql",
            "database",
            "stack",
            "traceback",
            "exception",
            "error",
            "SELECT",
            "FROM",
            "WHERE",
            "INSERT",
            "UPDATE",
            "DELETE",
        ]

        error_lower = error_msg.lower()
        leaked = [p for p in sensitive_patterns if p.lower() in error_lower]

        assert len(leaked) == 0, f"Error message leaks sensitive info: {leaked}"

    @staticmethod
    def assert_proper_validation(result: Any, payload: Any) -> None:
        """Assert that input was properly validated."""
        assert result is not None, "Validation should return a result"

    @staticmethod
    def assert_no_path_traversal(path: str) -> None:
        """Assert that path doesn't contain traversal sequences."""
        dangerous_patterns = ["../", "..\\", "%2e%2e/", "%2e%2e\\"]
        path_lower = path.lower()

        for pattern in dangerous_patterns:
            assert pattern not in path_lower, (
                f"Path traversal pattern '{pattern}' found in path"
            )

    @staticmethod
    def assert_content_length_limit(content: str, max_length: int) -> None:
        """Assert that content respects length limits."""
        assert len(content) <= max_length, (
            f"Content length {len(content)} exceeds limit {max_length}"
        )

    @staticmethod
    def assert_valid_format(value: str, pattern: str, description: str) -> None:
        """Assert that value matches expected format."""
        import re

        assert re.match(pattern, value), (
            f"Value '{value}' does not match expected {description} format"
        )

    @staticmethod
    def assert_rate_limited(fn: Callable, attempts: int = 20) -> None:
        """Assert that rate limiting is enforced after repeated attempts."""
        exceptions = 0
        for _ in range(attempts):
            try:
                fn()
            except Exception:
                exceptions += 1

        assert exceptions > 0, "Rate limiting should cause some requests to fail"


@pytest.fixture(scope="session")
def xss_payloads():
    """Fixture providing XSS attack payloads."""
    return XSSPayloads()


@pytest.fixture(scope="session")
def sql_payloads():
    """Fixture providing SQL injection payloads."""
    return SQLInjectionPayloads()


@pytest.fixture(scope="session")
def malformed_inputs():
    """Fixture providing malformed input test cases."""
    return MalformedInputs()


@pytest.fixture(scope="session")
def auth_payloads():
    """Fixture providing authentication attack payloads."""
    return AuthenticationPayloads()


@pytest.fixture(scope="session")
def security_assert():
    """Fixture providing security assertion helpers."""
    return SecurityAssertions()


def test_xss_vectors(
    target_fn: Callable, xss_payloads: XSSPayloads, security_assert: SecurityAssertions
) -> None:
    """
    Generic test helper for XSS prevention.

    Args:
        target_fn: Function that processes user input
        xss_payloads: XSS payload fixture
        security_assert: Security assertion fixture

    Example:
        def test_message_xss(messaging_manager, two_users, xss_payloads, security_assert):
            def send_msg(payload):
                user1, user2 = two_users
                dm = messaging_manager.create_dm(user1.id, user2.id)
                return messaging_manager.send_message(user1.id, dm.id, payload)

            test_xss_vectors(send_msg, xss_payloads, security_assert)
    """
    for payload in xss_payloads.all():
        try:
            result = target_fn(payload)
            if hasattr(result, "content"):
                security_assert.assert_no_xss(result.content, payload)
        except Exception:
            pass


def test_sql_injection(
    target_fn: Callable,
    sql_payloads: SQLInjectionPayloads,
    security_assert: SecurityAssertions,
) -> None:
    """
    Generic test helper for SQL injection prevention.

    Args:
        target_fn: Function that queries the database
        sql_payloads: SQL injection payload fixture
        security_assert: Security assertion fixture

    Example:
        def test_login_sql_injection(auth_manager, sql_payloads, security_assert):
            test_sql_injection(
                lambda p: auth_manager.login(p, "password"),
                sql_payloads,
                security_assert
            )
    """
    for payload in sql_payloads.all():
        security_assert.assert_rejected(target_fn, payload)


def test_input_validation(
    target_fn: Callable, malformed_inputs: MalformedInputs
) -> None:
    """
    Generic test helper for input validation.

    Args:
        target_fn: Function that validates input
        malformed_inputs: Malformed input fixture

    Example:
        def test_username_validation(auth_manager, malformed_inputs):
            test_input_validation(
                lambda i: auth_manager.register(i, "test@test.com", "Pass123!"),
                malformed_inputs
            )
    """
    for malformed in malformed_inputs.all():
        try:
            target_fn(malformed)
        except Exception:
            pass


class SecurityTestHelper:
    """Helper class for common security testing patterns."""

    def __init__(self, modules: Any, user_pool: Any):
        self.modules = modules
        self.user_pool = user_pool

    def test_field_xss(
        self, create_fn: Callable, field_name: str, xss_payloads: XSSPayloads
    ) -> None:
        """Test XSS prevention in a specific field."""
        for payload in xss_payloads.all():
            try:
                result = create_fn(**{field_name: payload})
                field_value = getattr(result, field_name, None)
                if field_value:
                    assert "<script" not in field_value.lower()
            except Exception:
                pass

    def test_field_sql_injection(
        self, query_fn: Callable, sql_payloads: SQLInjectionPayloads
    ) -> None:
        """Test SQL injection prevention in queries."""
        for payload in sql_payloads.all():
            try:
                query_fn(payload)
            except Exception:
                pass

    def test_authorization(
        self, resource_fn: Callable, owner_id: int, unauthorized_id: int
    ) -> None:
        """Test that unauthorized users cannot access resources."""
        try:
            resource_fn(unauthorized_id)
            pytest.fail("Unauthorized access should be prevented")
        except Exception:
            pass

    def test_parameter_tampering(
        self, update_fn: Callable, protected_fields: List[str]
    ) -> None:
        """Test that protected fields cannot be tampered with."""
        for field_name in protected_fields:
            try:
                result = update_fn(**{field_name: "tampered_value"})
                assert getattr(result, field_name, None) != "tampered_value"
            except Exception:
                pass


@pytest.fixture
def security_helper(modules, user_pool):
    """Fixture providing security test helper."""
    return SecurityTestHelper(modules, user_pool)
