"""
Password hashing and validation.

Uses the encryption module's Argon2id implementation for hashing.
Provides configurable password strength validation.
"""

import re
import sys
import os
from typing import List, Tuple

# Add paths for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
if common_utils_path not in sys.path:
    sys.path.append(common_utils_path)

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


def get_password_config() -> dict:
    """Get password configuration from config system."""
    auth_config = config.get("authentication", {})
    return auth_config.get("password", {
        "min_length": 12,
        "max_length": 128,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_digit": True,
        "require_special": True,
    })


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
    
    return PasswordValidation(
        valid=len(issues) == 0,
        score=score,
        issues=issues
    )


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
    reserved = {"admin", "administrator", "system", "bot", "api", "root", "null", "undefined"}
    if username.lower() in reserved:
        issues.append("This username is reserved")
    
    return len(issues) == 0, issues


def validate_email(email: str) -> bool:
    """
    Validate email format.
    
    Args:
        email: Email to validate
        
    Returns:
        True if email format is valid
    """
    # Basic email regex - not exhaustive but catches most issues
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))
