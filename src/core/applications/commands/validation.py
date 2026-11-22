"""
Command validation - Name and structure validation.
"""

import re
import unicodedata
from typing import Dict, Any, Tuple, List

from ..models import CommandType
from ..oauth.scopes import validate_permission_string
from src.core.security.hardening import HardeningViolation


# ASCII-only, fullmatch patterns. Python's ``\w`` matches every Unicode word
# character (fullwidth letters, Cyrillic lookalikes, dotless i, ...), which let
# a confusable be stored verbatim and rendered as a different ASCII name by a
# NFKC-normalizing client. Command names are an ASCII contract, so reject the
# lookalikes outright instead of trying to canonicalize them.
COMMAND_NAME_PATTERN = re.compile(r"[a-z0-9_-]{1,32}")
CONTEXT_MENU_NAME_PATTERN = re.compile(r"[A-Za-z0-9_ -]{1,32}")


def _is_confusable_safe(name: str) -> bool:
    """True when ``name`` is ASCII-only and stable under NFKC normalization.

    Compatibility characters (fullwidth forms, the Kelvin sign, ligatures),
    non-Latin homoglyphs and invisible/bidi format characters all fail one of
    these checks, so no stored command name can spoof a different one.
    """
    return name.isascii() and unicodedata.normalize("NFKC", name) == name


MAX_COMMAND_NAME_LENGTH = 32
MAX_COMMAND_DESCRIPTION_LENGTH = 100
MAX_OPTIONS_PER_COMMAND = 25


def validate_command_name(
    name: str, command_type: CommandType = CommandType.CHAT_INPUT
) -> Tuple[bool, List[str]]:
    """
    Validate a command name.

    Args:
        name: Command name
        command_type: Type of command

    Returns:
        Tuple of (valid, issues)
    """
    issues = []

    if not name:
        issues.append("Command name is required")
        return False, issues

    if len(name) > MAX_COMMAND_NAME_LENGTH:
        issues.append(f"Command name exceeds {MAX_COMMAND_NAME_LENGTH} characters")

    if command_type == CommandType.CHAT_INPUT:
        if not COMMAND_NAME_PATTERN.fullmatch(name) or not _is_confusable_safe(name):
            issues.append(
                "Chat command name must be lowercase ASCII letters, numbers, hyphens, and underscores"
            )

        if name != name.lower():
            issues.append("Chat command name must be lowercase")
    else:
        if not CONTEXT_MENU_NAME_PATTERN.fullmatch(name) or not _is_confusable_safe(
            name
        ):
            issues.append(
                "Context menu command name can only contain ASCII letters, numbers, hyphens, underscores, and spaces"
            )

    return len(issues) == 0, issues


def validate_command_description(
    description: str, command_type: CommandType
) -> Tuple[bool, List[str]]:
    """
    Validate a command description.

    Args:
        description: Command description
        command_type: Type of command

    Returns:
        Tuple of (valid, issues)
    """
    issues = []

    if command_type == CommandType.CHAT_INPUT:
        if not description:
            issues.append("Chat input commands require a description")
        elif len(description) > MAX_COMMAND_DESCRIPTION_LENGTH:
            issues.append(
                f"Command description exceeds {MAX_COMMAND_DESCRIPTION_LENGTH} characters"
            )
    else:
        if description and len(description) > MAX_COMMAND_DESCRIPTION_LENGTH:
            issues.append(
                f"Command description exceeds {MAX_COMMAND_DESCRIPTION_LENGTH} characters"
            )

    return len(issues) == 0, issues


def validate_command(command_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate complete command data.

    Args:
        command_data: Command data dict

    Returns:
        Tuple of (valid, issues)
    """
    issues = []

    command_type = command_data.get("command_type", CommandType.CHAT_INPUT)
    if isinstance(command_type, int):
        try:
            command_type = CommandType(command_type)
        except ValueError:
            issues.append(f"Invalid command type: {command_type}")
            command_type = CommandType.CHAT_INPUT

    name = command_data.get("name", "")
    name_valid, name_issues = validate_command_name(name, command_type)
    issues.extend(name_issues)

    description = command_data.get("description", "")
    desc_valid, desc_issues = validate_command_description(description, command_type)
    issues.extend(desc_issues)

    options = command_data.get("options", [])
    if options:
        if command_type != CommandType.CHAT_INPUT:
            issues.append("Only chat input commands can have options")
        elif len(options) > MAX_OPTIONS_PER_COMMAND:
            issues.append(f"Command exceeds {MAX_OPTIONS_PER_COMMAND} options")
        else:
            from .options import validate_options

            opts_valid, opts_issues = validate_options(options)
            issues.extend(opts_issues)

    default_perms = command_data.get("default_member_permissions")
    if default_perms is not None:
        try:
            validate_permission_string(
                default_perms, field="default_member_permissions"
            )
        except (HardeningViolation, TypeError, ValueError):
            issues.append(
                "default_member_permissions must be a valid permission integer string"
            )

    return len(issues) == 0, issues


def validate_command_update(update_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate command update data.

    Args:
        update_data: Update data dict

    Returns:
        Tuple of (valid, issues)
    """
    issues = []

    if "name" in update_data:
        command_type = update_data.get("command_type", CommandType.CHAT_INPUT)
        if isinstance(command_type, int):
            try:
                command_type = CommandType(command_type)
            except ValueError:
                command_type = CommandType.CHAT_INPUT

        name_valid, name_issues = validate_command_name(
            update_data["name"], command_type
        )
        issues.extend(name_issues)

    if "description" in update_data:
        command_type = update_data.get("command_type", CommandType.CHAT_INPUT)
        if isinstance(command_type, int):
            try:
                command_type = CommandType(command_type)
            except ValueError:
                command_type = CommandType.CHAT_INPUT

        desc_valid, desc_issues = validate_command_description(
            update_data["description"], command_type
        )
        issues.extend(desc_issues)

    if "options" in update_data:
        options = update_data["options"]
        if options and len(options) > MAX_OPTIONS_PER_COMMAND:
            issues.append(f"Command exceeds {MAX_OPTIONS_PER_COMMAND} options")
        elif options:
            from .options import validate_options

            opts_valid, opts_issues = validate_options(options)
            issues.extend(opts_issues)

    # SECURITY: mirror the registration-time check. `default_member_permissions`
    # can also be changed through update_command; without this guard an
    # attempter could register a command with a valid decimal permission
    # bitfield and then update it to an arbitrary value ("*", "-1", a huge
    # number, ...) that the command vocabulary rejects on creation. Validating
    # on both paths keeps the permission grammar consistent in both directions.
    default_perms = update_data.get("default_member_permissions")
    if default_perms is not None:
        try:
            validate_permission_string(
                default_perms, field="default_member_permissions"
            )
        except (HardeningViolation, TypeError, ValueError):
            issues.append(
                "default_member_permissions must be a valid permission integer string"
            )

    return len(issues) == 0, issues
