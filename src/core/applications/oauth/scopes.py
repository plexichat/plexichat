"""
OAuth2 scopes - Scope definitions and validation.
"""

from typing import List, Tuple


VALID_SCOPES = {
    "identify",
    "email",
    "guilds",
    "guilds.join",
    "guilds.members.read",
    "bot",
    "applications.commands",
    "applications.commands.update",
    "messages.read",
    "webhook.incoming",
}

SCOPE_DESCRIPTIONS = {
    "identify": "Access your username, avatar, and banner",
    "email": "Access your email address",
    "guilds": "Know what servers you are in",
    "guilds.join": "Join servers on your behalf",
    "guilds.members.read": "Read your member info in servers",
    "bot": "Add a bot to a server",
    "applications.commands": "Create commands in servers you manage",
    "applications.commands.update": "Update application commands",
    "messages.read": "Read your messages",
    "webhook.incoming": "Create webhooks in channels you manage",
}

BOT_REQUIRED_SCOPES = {"bot"}

PRIVILEGED_SCOPES = {
    "guilds.members.read",
    "messages.read",
}


def validate_scopes(scopes: List[str]) -> Tuple[bool, List[str]]:
    """
    Validate a list of scopes.
    
    Args:
        scopes: List of scope strings
        
    Returns:
        Tuple of (valid, issues)
    """
    issues = []

    if not scopes:
        issues.append("At least one scope is required")
        return False, issues

    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        issues.append(f"Invalid scopes: {', '.join(sorted(invalid))}")

    if "bot" in scopes and len(scopes) == 1:
        pass

    return len(issues) == 0, issues


def parse_scopes(scope_string: str) -> List[str]:
    """
    Parse a space-separated scope string.
    
    Args:
        scope_string: Space-separated scopes
        
    Returns:
        List of scope strings
    """
    if not scope_string:
        return []
    return [s.strip() for s in scope_string.split() if s.strip()]


def scopes_to_string(scopes: List[str]) -> str:
    """
    Convert a list of scopes to a space-separated string.
    
    Args:
        scopes: List of scope strings
        
    Returns:
        Space-separated scope string
    """
    return " ".join(sorted(scopes))


def has_scope(granted_scopes: List[str], required_scope: str) -> bool:
    """
    Check if a required scope is in the granted scopes.
    
    Args:
        granted_scopes: List of granted scopes
        required_scope: Scope to check for
        
    Returns:
        True if scope is granted
    """
    return required_scope in granted_scopes


def get_scope_description(scope: str) -> str:
    """
    Get the human-readable description for a scope.
    
    Args:
        scope: Scope string
        
    Returns:
        Description string
    """
    return SCOPE_DESCRIPTIONS.get(scope, scope)
