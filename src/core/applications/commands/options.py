"""
Command options - Option building and validation.
"""

import re
from typing import List, Dict, Any, Tuple, Optional

from ..models import CommandOption, CommandOptionType, CommandChoice


OPTION_NAME_PATTERN = re.compile(r"^[\w-]{1,32}$")
MAX_CHOICES = 25
MAX_OPTIONS = 25
MAX_OPTION_NAME_LENGTH = 32
MAX_OPTION_DESCRIPTION_LENGTH = 100
MAX_CHOICE_NAME_LENGTH = 100


def build_option(
    name: str,
    description: str,
    option_type: CommandOptionType,
    required: bool = False,
    choices: Optional[List[Dict[str, Any]]] = None,
    options: Optional[List[Dict[str, Any]]] = None,
    channel_types: Optional[List[int]] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    min_length: Optional[int] = None,
    max_length: Optional[int] = None,
    autocomplete: bool = False,
) -> CommandOption:
    """
    Build a command option.
    
    Args:
        name: Option name
        description: Option description
        option_type: Type of option
        required: Whether option is required
        choices: List of choices for string/integer/number options
        options: Sub-options for sub-command groups
        channel_types: Allowed channel types for channel options
        min_value: Minimum value for integer/number options
        max_value: Maximum value for integer/number options
        min_length: Minimum length for string options
        max_length: Maximum length for string options
        autocomplete: Whether to enable autocomplete
        
    Returns:
        CommandOption
    """
    parsed_choices = None
    if choices:
        parsed_choices = [
            {"name": c["name"], "value": c["value"]}
            for c in choices
        ]
    
    parsed_options = None
    if options:
        parsed_options = [
            build_option(**opt) if isinstance(opt, dict) else opt
            for opt in options
        ]
    
    return CommandOption(
        name=name.lower(),
        description=description,
        option_type=option_type,
        required=required,
        choices=parsed_choices,
        options=parsed_options,
        channel_types=channel_types,
        min_value=min_value,
        max_value=max_value,
        min_length=min_length,
        max_length=max_length,
        autocomplete=autocomplete,
    )


def validate_option(option: Dict[str, Any], depth: int = 0) -> Tuple[bool, List[str]]:
    """
    Validate a command option.
    
    Args:
        option: Option data dict
        depth: Current nesting depth
        
    Returns:
        Tuple of (valid, issues)
    """
    issues = []
    
    name = option.get("name", "")
    if not name:
        issues.append("Option name is required")
    elif len(name) > MAX_OPTION_NAME_LENGTH:
        issues.append(f"Option name exceeds {MAX_OPTION_NAME_LENGTH} characters")
    elif not OPTION_NAME_PATTERN.match(name):
        issues.append(f"Option name '{name}' contains invalid characters")
    
    description = option.get("description", "")
    if not description:
        issues.append(f"Option '{name}' requires a description")
    elif len(description) > MAX_OPTION_DESCRIPTION_LENGTH:
        issues.append(f"Option '{name}' description exceeds {MAX_OPTION_DESCRIPTION_LENGTH} characters")
    
    option_type = option.get("option_type") or option.get("type")
    if option_type is None:
        issues.append(f"Option '{name}' requires a type")
    else:
        try:
            if isinstance(option_type, int):
                CommandOptionType(option_type)
            elif isinstance(option_type, CommandOptionType):
                pass
            else:
                issues.append(f"Option '{name}' has invalid type")
        except ValueError:
            issues.append(f"Option '{name}' has invalid type value: {option_type}")
    
    choices = option.get("choices")
    if choices:
        if len(choices) > MAX_CHOICES:
            issues.append(f"Option '{name}' exceeds {MAX_CHOICES} choices")
        
        for i, choice in enumerate(choices):
            if not choice.get("name"):
                issues.append(f"Option '{name}' choice {i} requires a name")
            elif len(choice["name"]) > MAX_CHOICE_NAME_LENGTH:
                issues.append(f"Option '{name}' choice {i} name exceeds {MAX_CHOICE_NAME_LENGTH} characters")
            
            if "value" not in choice:
                issues.append(f"Option '{name}' choice {i} requires a value")
    
    autocomplete = option.get("autocomplete", False)
    if autocomplete and choices:
        issues.append(f"Option '{name}' cannot have both autocomplete and choices")
    
    sub_options = option.get("options")
    if sub_options:
        if depth >= 2:
            issues.append(f"Option '{name}' exceeds maximum nesting depth")
        else:
            if len(sub_options) > MAX_OPTIONS:
                issues.append(f"Option '{name}' exceeds {MAX_OPTIONS} sub-options")
            
            for sub_opt in sub_options:
                sub_valid, sub_issues = validate_option(sub_opt, depth + 1)
                issues.extend(sub_issues)
    
    return len(issues) == 0, issues


def validate_options(options: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """
    Validate a list of command options.
    
    Args:
        options: List of option data dicts
        
    Returns:
        Tuple of (valid, issues)
    """
    issues = []
    
    if len(options) > MAX_OPTIONS:
        issues.append(f"Command exceeds {MAX_OPTIONS} options")
    
    names = set()
    for opt in options:
        name = opt.get("name", "").lower()
        if name in names:
            issues.append(f"Duplicate option name: {name}")
        names.add(name)
        
        valid, opt_issues = validate_option(opt)
        issues.extend(opt_issues)
    
    required_ended = False
    for opt in options:
        if opt.get("required", False):
            if required_ended:
                issues.append("Required options must come before optional options")
                break
        else:
            required_ended = True
    
    return len(issues) == 0, issues


def options_to_dict(options: List[CommandOption]) -> List[Dict[str, Any]]:
    """
    Convert command options to dict format.
    
    Args:
        options: List of CommandOption
        
    Returns:
        List of option dicts
    """
    result = []
    for opt in options:
        opt_dict = {
            "name": opt.name,
            "description": opt.description,
            "type": opt.option_type.value if isinstance(opt.option_type, CommandOptionType) else opt.option_type,
            "required": opt.required,
        }
        
        if opt.choices:
            opt_dict["choices"] = opt.choices
        
        if opt.options:
            opt_dict["options"] = options_to_dict(opt.options)
        
        if opt.channel_types:
            opt_dict["channel_types"] = opt.channel_types
        
        if opt.min_value is not None:
            opt_dict["min_value"] = opt.min_value
        
        if opt.max_value is not None:
            opt_dict["max_value"] = opt.max_value
        
        if opt.min_length is not None:
            opt_dict["min_length"] = opt.min_length
        
        if opt.max_length is not None:
            opt_dict["max_length"] = opt.max_length
        
        if opt.autocomplete:
            opt_dict["autocomplete"] = opt.autocomplete
        
        result.append(opt_dict)
    
    return result


def options_from_dict(options_data: List[Dict[str, Any]]) -> List[CommandOption]:
    """
    Convert dict format to command options.
    
    Args:
        options_data: List of option dicts
        
    Returns:
        List of CommandOption
    """
    result = []
    for opt_dict in options_data:
        option_type = opt_dict.get("type") or opt_dict.get("option_type")
        if option_type is None:
            raise ValueError(f"Option {opt_dict.get('name')} missing type")
        if isinstance(option_type, int):
            option_type = CommandOptionType(option_type)
        
        sub_options = None
        if opt_dict.get("options"):
            sub_options = options_from_dict(opt_dict["options"])
        
        result.append(CommandOption(
            name=opt_dict["name"],
            description=opt_dict["description"],
            option_type=option_type,
            required=opt_dict.get("required", False),
            choices=opt_dict.get("choices"),
            options=sub_options,
            channel_types=opt_dict.get("channel_types"),
            min_value=opt_dict.get("min_value"),
            max_value=opt_dict.get("max_value"),
            min_length=opt_dict.get("min_length"),
            max_length=opt_dict.get("max_length"),
            autocomplete=opt_dict.get("autocomplete", False),
        ))
    
    return result
