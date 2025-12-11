"""
Command validation - Name and structure validation.
"""

import re
from typing import Dict, Any, Tuple, List

from ..models import CommandType


COMMAND_NAME_PATTERN = re.compile(r"^[\w-]{1,32}$")
MAX_COMMAND_NAME_LENGTH = 32
MAX_COMMAND_DESCRIPTION_LENGTH = 100
MAX_OPTIONS_PER_COMMAND = 25


def validate_command_name(name: str) -> Tuple[bool, List[str]]:
    """
    Validate a command name.
    
    Args:
        name: Command name
        
    Returns:
        Tuple of (valid, issues)
    """
    issues = []

    if not name:
        issues.append("Command name is required")
        return False, issues

    if len(name) > MAX_COMMAND_NAME_LENGTH:
        issues.append(f"Command name exceeds {MAX_COMMAND_NAME_LENGTH} characters")

    if not COMMAND_NAME_PATTERN.match(name):
        issues.append("Command name must be lowercase and contain only letters, numbers, hyphens, and underscores")

    if name != name.lower():
        issues.append("Command name must be lowercase")

    return len(issues) == 0, issues


def validate_command_description(description: str, command_type: CommandType) -> Tuple[bool, List[str]]:
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
            issues.append(f"Command description exceeds {MAX_COMMAND_DESCRIPTION_LENGTH} characters")
    else:
        if description and len(description) > MAX_COMMAND_DESCRIPTION_LENGTH:
            issues.append(f"Command description exceeds {MAX_COMMAND_DESCRIPTION_LENGTH} characters")

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

    name = command_data.get("name", "")
    name_valid, name_issues = validate_command_name(name)
    issues.extend(name_issues)

    command_type = command_data.get("command_type", CommandType.CHAT_INPUT)
    if isinstance(command_type, int):
        try:
            command_type = CommandType(command_type)
        except ValueError:
            issues.append(f"Invalid command type: {command_type}")
            command_type = CommandType.CHAT_INPUT

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
            int(default_perms)
        except (ValueError, TypeError):
            issues.append("default_member_permissions must be a valid permission integer string")

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
        name_valid, name_issues = validate_command_name(update_data["name"])
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

    return len(issues) == 0, issues
