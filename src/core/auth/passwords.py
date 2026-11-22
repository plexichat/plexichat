"""
Password management module.

This module handles password changes, reset requests, resets, and validation.
"""

import re
import secrets
import string
import unicodedata
from pathlib import Path
from typing import List, Tuple, Set

import utils.config as config
from src.core.security.errors import TextRejected
from src.core.security.policies import RejectUICharacters
from .models import PasswordValidation
from ._lazy import _get_auth_manager


# Hard ceiling independent of configuration. Every credential-setting path
# (registration, change, reset) validates before hashing, so this guarantees an
# unbounded string can never be handed to the password hasher even if an
# operator misconfigures ``password.max_length`` or an internal caller skips
# the API schema. This matches the request-schema cap of 1024 characters.
_ABSOLUTE_MAX_PASSWORD_LENGTH = 1024

# Every glyph here is a member of the ``require_special`` regex enforced by
# ``validate_password`` below, so a password carrying one of them can never be
# rejected for lacking a special character.
_GENERATED_PASSWORD_SPECIALS = "!@#$%^&*"


def generate_policy_compliant_password(token_bytes: int = 32) -> str:
    """Return a CSPRNG password that always satisfies ``validate_password``.

    ``secrets.token_urlsafe(32)`` carries ~256 bits of entropy, but its
    base64url alphabet only *probabilistically* contains the classes the
    policy requires. Only ``-`` and ``_`` count as "special" and ~26% of draws
    contain neither, so callers that hand the raw token to ``register`` (most
    notably OAuth auto-link/registration) intermittently get a
    ``WeakPasswordError`` even though the token is cryptographically strong.
    Appending one guaranteed character from every class the policy requires
    preserves the full entropy of the token while making compliance
    deterministic.
    """
    base = secrets.token_urlsafe(token_bytes)
    policy = get_password_config()

    guaranteed: List[str] = []
    if policy.get("require_uppercase", True):
        guaranteed.append(secrets.choice(string.ascii_uppercase))
    if policy.get("require_lowercase", True):
        guaranteed.append(secrets.choice(string.ascii_lowercase))
    if policy.get("require_digit", True):
        guaranteed.append(secrets.choice(string.digits))
    if policy.get("require_special", True):
        guaranteed.append(secrets.choice(_GENERATED_PASSWORD_SPECIALS))
    suffix = "".join(guaranteed)

    password = base + suffix
    # Respect the configured ceiling; trim only the CSPRNG tail if the
    # guaranteed suffix would otherwise push the password over max_length.
    max_length = min(policy.get("max_length", 128), _ABSOLUTE_MAX_PASSWORD_LENGTH)
    if len(password) > max_length:
        password = base[: max(0, max_length - len(suffix))] + suffix
    return password


def _password_policy_forms(value: str) -> Set[str]:
    """Return the string forms used for password policy comparisons.

    ``casefold()`` alone does not fold Unicode compatibility characters:
    fullwidth letters (``U+FF21``..), the Kelvin sign, ligatures, etc. survive
    casefolding and therefore bypass a blacklist built from plain ASCII
    entries. NFKC canonicalizes those confusables back to their ASCII
    equivalents, so ``Password123!`` and its lookalike ``Pａssword123!`` are
    recognized as the same password. Both the raw casefolded form and the
    NFKC+casefolded form are returned so the check can never become *less*
    strict than the original exact casefold match.
    """
    forms = {value.casefold()}
    try:
        normalized = unicodedata.normalize("NFKC", value)
        # Casefolding can itself introduce characters that need a second
        # normalization pass (NFKC_casefold).
        forms.add(unicodedata.normalize("NFKC", normalized.casefold()))
    except TypeError:
        # Non-string input is rejected by the callers' type checks; never let
        # a malformed value turn a policy check into a 500.
        pass
    return forms


def validate_username(username: str) -> Tuple[bool, List[str]]:
    """
    Validate username format.

    Args:
        username: Username to validate

    Returns:
        Tuple of (valid, issues)
    """
    auth_config = config.get("authentication", {})
    accounts_config = auth_config.get("accounts", {})

    min_length = accounts_config.get("username_min_length", 3)
    max_length = accounts_config.get("username_max_length", 32)
    pattern = accounts_config.get("username_pattern", r"^[a-zA-Z0-9_]+$")

    issues = []

    if len(username) < min_length:
        issues.append(f"Username must be at least {min_length} characters")

    if len(username) > max_length:
        issues.append(f"Username must be at most {max_length} characters")

    # fullmatch (not match): ``$`` in the configured pattern matches before a
    # trailing newline, so ``re.match(r"^[a-zA-Z0-9_]+$", "00\n")`` succeeds
    # and control characters slipped through the validator. fullmatch requires
    # the pattern to cover the entire string, rejecting any trailing input.
    if not re.fullmatch(pattern, username):
        issues.append("Username can only contain letters, numbers, and underscores")

    reserved = {
        "admin",
        "administrator",
        "system",
        "bot",
        "api",
        "root",
        "null",
        "undefined",
    }
    if username.lower() in reserved:
        issues.append("This username is reserved")

    # Defence in depth: reject HTML metacharacters and control characters
    # regardless of the configured username_pattern, so a loosened pattern can
    # never let XSS-relevant characters into stored usernames. The rule is the
    # shared policy object, not a local copy of the character set, so a change
    # to the definition cannot silently apply to names and not to usernames.
    try:
        RejectUICharacters().check(username, "Username")
    except TextRejected as rejection:
        issues.append(str(rejection))

    return len(issues) == 0, issues


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Change password. Requires current password."""
    return (
        _get_auth_manager()
        .get_instance()
        .change_password(user_id, old_password, new_password)
    )


def verify_user_password(user_id: int, password: str) -> bool:
    """Re-confirm a user's password without creating a session.

    Used by sensitive-action challenges (password/email change, 2FA
    setup) so they do not mint orphaned sessions via ``login()``.
    Returns False (never raises) when the user is missing or the
    password does not match; callers map that to 401/403.
    """
    return bool(
        _get_auth_manager().get_instance().verify_user_password(user_id, password)
    )


def request_password_reset(email: str) -> bool:
    """Request password reset email. Returns False if email not configured."""
    return _get_auth_manager().get_instance().request_password_reset(email)


def reset_password(token: str, new_password: str) -> bool:
    """Reset password with token from reset email."""
    return _get_auth_manager().get_instance().reset_password(token, new_password)


def get_password_config() -> dict:
    """Get password configuration from config system."""
    auth_config = config.get("authentication", {})
    return auth_config.get(
        "password",
        {
            "min_length": 12,
            "max_length": 128,
            "require_uppercase": True,
            "require_lowercase": True,
            "require_digit": True,
            "require_special": True,
        },
    )


def validate_password(password: str) -> PasswordValidation:
    """
    Validate password strength against configured requirements.

    Args:
        password: Password to validate

    Returns:
        PasswordValidation with valid flag, score, and issues
    """
    pwd_config = get_password_config()
    issues = []
    score = 0

    min_length = pwd_config.get("min_length", 12)
    max_length = min(pwd_config.get("max_length", 128), _ABSOLUTE_MAX_PASSWORD_LENGTH)

    if len(password) < min_length:
        issues.append(f"Password must be at least {min_length} characters")
    else:
        score += 1

    if len(password) > max_length:
        issues.append(f"Password must be at most {max_length} characters")

    # Reject common/breached passwords before composition checks. Keep the
    # list lazy-loaded and process-local so startup does not do file I/O, while
    # ensuring the bundled list is actually enforced for every password path.
    # Both the list entries and the candidate are reduced to NFKC+casefold
    # forms: casefolding by itself lets Unicode compatibility characters
    # (fullwidth letters, ligatures, the Kelvin sign, ...) stand in for ASCII
    # and evade what is otherwise an exact-match blacklist.
    common_passwords_enabled = pwd_config.get("reject_common_passwords", True)
    if common_passwords_enabled:
        common_path = Path(__file__).parent / "data" / "common-passwords.txt"
        try:
            common_passwords: Set[str] = getattr(
                validate_password, "_common_passwords", set()
            )
            if not common_passwords:
                common_passwords = set()
                for line in common_path.read_text(encoding="utf-8").splitlines():
                    entry = line.strip()
                    if entry:
                        common_passwords |= _password_policy_forms(entry)
                setattr(validate_password, "_common_passwords", common_passwords)
            if _password_policy_forms(password) & common_passwords:
                issues.append(
                    "Password is too common; choose a less predictable password"
                )
        except OSError as exc:
            # Fail CLOSED: the common/breached-password list is a security
            # control, not an optional hint. If it cannot be read we cannot
            # tell a strong password from a known-compromised one, so reject
            # the candidate instead of silently dropping the check.
            issues.append(
                "Password strength policy is temporarily unavailable; "
                f"please try again later ({exc.__class__.__name__})"
            )

    if pwd_config.get("require_uppercase", True):
        if not re.search(r"[A-Z]", password):
            issues.append("Password must contain at least one uppercase letter")
        else:
            score += 1

    if pwd_config.get("require_lowercase", True):
        if not re.search(r"[a-z]", password):
            issues.append("Password must contain at least one lowercase letter")
        else:
            score += 1

    if pwd_config.get("require_digit", True):
        if not re.search(r"\d", password):
            issues.append("Password must contain at least one digit")
        else:
            score += 1

    if pwd_config.get("require_special", True):
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>_\-+=\[\]\\;'/`~]", password):
            issues.append("Password must contain at least one special character")
        else:
            score += 1

    if len(password) >= 16:
        score += 1
    if len(password) >= 20:
        score += 1

    score = min(score, 5)

    return PasswordValidation(
        valid=len(issues) == 0,
        score=score,
        issues=issues,
    )


def validate_email(email: str) -> bool:
    """
    Validate email format.

    Args:
        email: Email to validate

    Returns:
        True if email format is valid
    """
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    # fullmatch (not match): same trailing-newline ``$`` gotcha as
    # validate_username — ``"a@b.com\n"`` would otherwise pass validation.
    return bool(re.fullmatch(pattern, email))


__all__ = [
    "change_password",
    "verify_user_password",
    "request_password_reset",
    "reset_password",
    "validate_password",
    "validate_username",
    "validate_email",
]
