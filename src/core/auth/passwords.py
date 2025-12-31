"""
Password hashing and validation.

Uses the encryption module's Argon2id implementation for hashing.
Provides configurable password strength validation.
"""

import re
from typing import List, Tuple, Dict, Any

import utils.config as config

# Import from our encryption module
from src.utils.encryption import hash_password as _hash, verify_password as _verify

from .models import PasswordValidation


def hash_password(password: str) -> str:
    """
    Hash a password using Argon2id.

    Args:
        password: Plain text password

    Returns:
        Hashed password string
    """
    return _hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against its hash.

    Args:
        password: Plain text password to verify
        password_hash: Stored hash to verify against

    Returns:
        True if password matches
    """
    return _verify(password, password_hash)


def get_password_config() -> Dict[str, Any]:
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

    # Length checks
    min_length = pwd_config.get("min_length", 12)
    max_length = pwd_config.get("max_length", 128)

    if len(password) < min_length:
        issues.append(f"Password must be at least {min_length} characters")
    else:
        score += 1

    if len(password) > max_length:
        issues.append(f"Password must be at most {max_length} characters")

    # Complexity checks
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

    # Bonus points for extra length
    if len(password) >= 16:
        score += 1
    if len(password) >= 20:
        score += 1

    # Cap score at 5
    score = min(score, 5)

    return PasswordValidation(valid=len(issues) == 0, score=score, issues=issues)


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

    if not re.match(pattern, username):
        issues.append("Username can only contain letters, numbers, and underscores")

    # Reserved usernames
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

    return len(issues) == 0, issues


def validate_email(email: str) -> bool:
    """
    Validate email format including TLD validation.

    Args:
        email: Email to validate

    Returns:
        True if email format is valid with a recognized TLD
    """
    import utils.logger as logger
    
    # Basic email regex - not exhaustive but catches most issues
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.([a-zA-Z]{2,})$"
    match = re.match(pattern, email)
    if not match:
        logger.debug(f"Email validation failed (regex): {email}")
        return False

    # Extract and validate TLD
    tld = match.group(1).lower()

    # Comprehensive list of valid TLDs (common gTLDs, ccTLDs, and new gTLDs)
    valid_tlds = {
        # Generic TLDs
        "com",
        "org",
        "net",
        "edu",
        "gov",
        "mil",
        "int",
        "info",
        "biz",
        "name",
        "pro",
        "aero",
        "coop",
        "museum",
        "jobs",
        "travel",
        "mobi",
        "cat",
        "asia",
        "tel",
        "xxx",
        "post",
        # Common new gTLDs
        "app",
        "dev",
        "io",
        "co",
        "ai",
        "me",
        "tv",
        "cc",
        "ws",
        "fm",
        "am",
        "ly",
        "to",
        "gg",
        "xyz",
        "online",
        "site",
        "website",
        "tech",
        "store",
        "shop",
        "blog",
        "cloud",
        "digital",
        "email",
        "global",
        "group",
        "live",
        "network",
        "news",
        "social",
        "space",
        "studio",
        "team",
        "world",
        "zone",
        "agency",
        "company",
        "consulting",
        "design",
        "engineering",
        "expert",
        "marketing",
        "media",
        "photography",
        "services",
        "software",
        "solutions",
        "support",
        "systems",
        "technology",
        "ventures",
        "works",
        "academy",
        "center",
        "club",
        "community",
        "foundation",
        "institute",
        "international",
        "management",
        "partners",
        "plus",
        "pro",
        "training",
        "university",
        # Country code TLDs (comprehensive list)
        "ac",
        "ad",
        "ae",
        "af",
        "ag",
        "al",
        "am",
        "ao",
        "aq",
        "ar",
        "as",
        "at",
        "au",
        "aw",
        "ax",
        "az",
        "ba",
        "bb",
        "bd",
        "be",
        "bf",
        "bg",
        "bh",
        "bi",
        "bj",
        "bm",
        "bn",
        "bo",
        "br",
        "bs",
        "bt",
        "bw",
        "by",
        "bz",
        "ca",
        "cd",
        "cf",
        "cg",
        "ch",
        "ci",
        "ck",
        "cl",
        "cm",
        "cn",
        "cr",
        "cu",
        "cv",
        "cw",
        "cx",
        "cy",
        "cz",
        "de",
        "dj",
        "dk",
        "dm",
        "do",
        "dz",
        "ec",
        "ee",
        "eg",
        "er",
        "es",
        "et",
        "eu",
        "fi",
        "fj",
        "fk",
        "fo",
        "fr",
        "ga",
        "gb",
        "gd",
        "ge",
        "gf",
        "gh",
        "gi",
        "gl",
        "gm",
        "gn",
        "gp",
        "gq",
        "gr",
        "gs",
        "gt",
        "gu",
        "gw",
        "gy",
        "hk",
        "hm",
        "hn",
        "hr",
        "ht",
        "hu",
        "id",
        "ie",
        "il",
        "im",
        "in",
        "iq",
        "ir",
        "is",
        "it",
        "je",
        "jm",
        "jo",
        "jp",
        "ke",
        "kg",
        "kh",
        "ki",
        "km",
        "kn",
        "kp",
        "kr",
        "kw",
        "ky",
        "kz",
        "la",
        "lb",
        "lc",
        "li",
        "lk",
        "lr",
        "ls",
        "lt",
        "lu",
        "lv",
        "ly",
        "ma",
        "mc",
        "md",
        "mg",
        "mh",
        "mk",
        "ml",
        "mm",
        "mn",
        "mo",
        "mp",
        "mq",
        "mr",
        "ms",
        "mt",
        "mu",
        "mv",
        "mw",
        "mx",
        "my",
        "mz",
        "na",
        "nc",
        "ne",
        "nf",
        "ng",
        "ni",
        "nl",
        "no",
        "np",
        "nr",
        "nu",
        "nz",
        "om",
        "pa",
        "pe",
        "pf",
        "pg",
        "ph",
        "pk",
        "pl",
        "pm",
        "pn",
        "pr",
        "ps",
        "pt",
        "pw",
        "py",
        "qa",
        "re",
        "ro",
        "rs",
        "ru",
        "rw",
        "sa",
        "sb",
        "sc",
        "sd",
        "se",
        "sg",
        "sh",
        "si",
        "sk",
        "sl",
        "sm",
        "sn",
        "so",
        "sr",
        "ss",
        "st",
        "su",
        "sv",
        "sx",
        "sy",
        "sz",
        "tc",
        "td",
        "tf",
        "tg",
        "th",
        "tj",
        "tk",
        "tl",
        "tm",
        "tn",
        "tr",
        "tt",
        "tw",
        "tz",
        "ua",
        "ug",
        "uk",
        "us",
        "uy",
        "uz",
        "va",
        "vc",
        "ve",
        "vg",
        "vi",
        "vn",
        "vu",
        "wf",
        "ws",
        "ye",
        "yt",
        "za",
        "zm",
        "zw",
        # Regional/special TLDs
        "asia",
        "eu",
        "africa",
        "lat",
        "scot",
        "wales",
        "cymru",
        "london",
        "nyc",
        "paris",
        "berlin",
        "tokyo",
        "moscow",
        "quebec",
        "bayern",
        "nrw",
        "koeln",
        "hamburg",
        "wien",
        "brussels",
        "amsterdam",
        "barcelona",
        "madrid",
        "roma",
        # Brand/corporate TLDs (common ones)
        "google",
        "apple",
        "microsoft",
        "amazon",
        "netflix",
        "youtube",
        "gmail",
        # Other common TLDs
        "link",
        "click",
        "help",
        "how",
        "here",
        "page",
        "web",
        "one",
        "top",
        "vip",
        "win",
        "bid",
        "trade",
        "review",
        "date",
        "download",
        "stream",
        "racing",
        "cricket",
        "party",
        "science",
        "work",
        "money",
        "cash",
        "fund",
        "financial",
        "exchange",
        "market",
        "capital",
        "investments",
        "holdings",
        "limited",
        "gmbh",
        "ltda",
        "sarl",
        "srl",
    }

    return tld in valid_tlds
