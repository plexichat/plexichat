"""
Component builders - Build and validate message components.
"""

from typing import List, Dict, Any, Optional, Tuple

from ..models import (
    Button, SelectMenu, SelectOption, TextInput, ActionRow, Modal,
    ComponentType, ButtonStyle, TextInputStyle,
)


MAX_BUTTONS_PER_ROW = 5
MAX_SELECTS_PER_ROW = 1
MAX_ROWS = 5
MAX_CUSTOM_ID_LENGTH = 100
MAX_LABEL_LENGTH = 80
MAX_PLACEHOLDER_LENGTH = 150
MAX_SELECT_OPTIONS = 25
MAX_MODAL_COMPONENTS = 5


def build_button(
    style: ButtonStyle,
    label: Optional[str] = None,
    emoji: Optional[Dict[str, Any]] = None,
    custom_id: Optional[str] = None,
    url: Optional[str] = None,
    disabled: bool = False,
) -> Button:
    """
    Build a button component.
    
    Args:
        style: Button style
        label: Button label
        emoji: Button emoji
        custom_id: Custom ID for non-link buttons
        url: URL for link buttons
        disabled: Whether button is disabled
        
    Returns:
        Button
    """
    return Button(
        style=style,
        label=label,
        emoji=emoji,
        custom_id=custom_id,
        url=url,
        disabled=disabled,
    )


def build_select_menu(
    custom_id: str,
    component_type: ComponentType = ComponentType.STRING_SELECT,
    options: Optional[List[Dict[str, Any]]] = None,
    channel_types: Optional[List[int]] = None,
    placeholder: Optional[str] = None,
    min_values: int = 1,
    max_values: int = 1,
    disabled: bool = False,
) -> SelectMenu:
    """
    Build a select menu component.
    
    Args:
        custom_id: Custom ID
        component_type: Type of select menu
        options: Options for string select
        channel_types: Channel types for channel select
        placeholder: Placeholder text
        min_values: Minimum selections
        max_values: Maximum selections
        disabled: Whether menu is disabled
        
    Returns:
        SelectMenu
    """
    parsed_options = None
    if options:
        parsed_options = [
            SelectOption(
                label=opt["label"],
                value=opt["value"],
                description=opt.get("description"),
                emoji=opt.get("emoji"),
                default=opt.get("default", False),
            )
            for opt in options
        ]
    
    return SelectMenu(
        custom_id=custom_id,
        component_type=component_type,
        options=parsed_options,
        channel_types=channel_types,
        placeholder=placeholder,
        min_values=min_values,
        max_values=max_values,
        disabled=disabled,
    )


def build_text_input(
    custom_id: str,
    label: str,
    style: TextInputStyle = TextInputStyle.SHORT,
    min_length: Optional[int] = None,
    max_length: Optional[int] = None,
    required: bool = True,
    value: Optional[str] = None,
    placeholder: Optional[str] = None,
) -> TextInput:
    """
    Build a text input component.
    
    Args:
        custom_id: Custom ID
        label: Input label
        style: Input style
        min_length: Minimum length
        max_length: Maximum length
        required: Whether input is required
        value: Pre-filled value
        placeholder: Placeholder text
        
    Returns:
        TextInput
    """
    return TextInput(
        custom_id=custom_id,
        style=style,
        label=label,
        min_length=min_length,
        max_length=max_length,
        required=required,
        value=value,
        placeholder=placeholder,
    )


def build_action_row(components: List[Any]) -> ActionRow:
    """
    Build an action row container.
    
    Args:
        components: List of components
        
    Returns:
        ActionRow
    """
    return ActionRow(components=components)


def build_modal(
    custom_id: str,
    title: str,
    components: List[ActionRow],
) -> Modal:
    """
    Build a modal dialog.
    
    Args:
        custom_id: Custom ID
        title: Modal title
        components: List of action rows with text inputs
        
    Returns:
        Modal
    """
    return Modal(
        custom_id=custom_id,
        title=title,
        components=components,
    )


def validate_button(button: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a button component."""
    issues = []
    
    style = button.get("style")
    if style is None:
        issues.append("Button requires a style")
    else:
        try:
            if isinstance(style, int):
                ButtonStyle(style)
        except ValueError:
            issues.append(f"Invalid button style: {style}")
    
    label = button.get("label")
    emoji = button.get("emoji")
    if not label and not emoji:
        issues.append("Button requires a label or emoji")
    
    if label and len(label) > MAX_LABEL_LENGTH:
        issues.append(f"Button label exceeds {MAX_LABEL_LENGTH} characters")
    
    custom_id = button.get("custom_id")
    url = button.get("url")
    
    if style == ButtonStyle.LINK or style == 5:
        if not url:
            issues.append("Link button requires a URL")
        if custom_id:
            issues.append("Link button cannot have a custom_id")
    else:
        if not custom_id:
            issues.append("Non-link button requires a custom_id")
        if url:
            issues.append("Non-link button cannot have a URL")
    
    if custom_id and len(custom_id) > MAX_CUSTOM_ID_LENGTH:
        issues.append(f"custom_id exceeds {MAX_CUSTOM_ID_LENGTH} characters")
    
    return len(issues) == 0, issues


def validate_select_menu(select: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a select menu component."""
    issues = []
    
    custom_id = select.get("custom_id")
    if not custom_id:
        issues.append("Select menu requires a custom_id")
    elif len(custom_id) > MAX_CUSTOM_ID_LENGTH:
        issues.append(f"custom_id exceeds {MAX_CUSTOM_ID_LENGTH} characters")
    
    component_type = select.get("type", ComponentType.STRING_SELECT)
    if isinstance(component_type, int):
        try:
            component_type = ComponentType(component_type)
        except ValueError:
            issues.append(f"Invalid select menu type: {component_type}")
    
    if component_type == ComponentType.STRING_SELECT:
        options = select.get("options", [])
        if not options:
            issues.append("String select requires options")
        elif len(options) > MAX_SELECT_OPTIONS:
            issues.append(f"Select menu exceeds {MAX_SELECT_OPTIONS} options")
        
        for i, opt in enumerate(options):
            if not opt.get("label"):
                issues.append(f"Option {i} requires a label")
            if not opt.get("value"):
                issues.append(f"Option {i} requires a value")
    
    placeholder = select.get("placeholder")
    if placeholder and len(placeholder) > MAX_PLACEHOLDER_LENGTH:
        issues.append(f"Placeholder exceeds {MAX_PLACEHOLDER_LENGTH} characters")
    
    min_values = select.get("min_values", 1)
    max_values = select.get("max_values", 1)
    if min_values < 0 or min_values > 25:
        issues.append("min_values must be between 0 and 25")
    if max_values < 1 or max_values > 25:
        issues.append("max_values must be between 1 and 25")
    if min_values > max_values:
        issues.append("min_values cannot exceed max_values")
    
    return len(issues) == 0, issues


def validate_text_input(text_input: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a text input component."""
    issues = []
    
    custom_id = text_input.get("custom_id")
    if not custom_id:
        issues.append("Text input requires a custom_id")
    elif len(custom_id) > MAX_CUSTOM_ID_LENGTH:
        issues.append(f"custom_id exceeds {MAX_CUSTOM_ID_LENGTH} characters")
    
    label = text_input.get("label")
    if not label:
        issues.append("Text input requires a label")
    elif len(label) > 45:
        issues.append("Text input label exceeds 45 characters")
    
    style = text_input.get("style")
    if style is not None:
        try:
            if isinstance(style, int):
                TextInputStyle(style)
        except ValueError:
            issues.append(f"Invalid text input style: {style}")
    
    min_length = text_input.get("min_length")
    max_length = text_input.get("max_length")
    if min_length is not None and (min_length < 0 or min_length > 4000):
        issues.append("min_length must be between 0 and 4000")
    if max_length is not None and (max_length < 1 or max_length > 4000):
        issues.append("max_length must be between 1 and 4000")
    if min_length is not None and max_length is not None and min_length > max_length:
        issues.append("min_length cannot exceed max_length")
    
    placeholder = text_input.get("placeholder")
    if placeholder and len(placeholder) > MAX_PLACEHOLDER_LENGTH:
        issues.append(f"Placeholder exceeds {MAX_PLACEHOLDER_LENGTH} characters")
    
    return len(issues) == 0, issues


def validate_action_row(row: Dict[str, Any], context: str = "message") -> Tuple[bool, List[str]]:
    """Validate an action row."""
    issues = []
    
    components = row.get("components", [])
    if not components:
        issues.append("Action row requires at least one component")
        return False, issues
    
    component_types = set()
    for comp in components:
        comp_type = comp.get("type")
        if isinstance(comp_type, int):
            try:
                comp_type = ComponentType(comp_type)
            except ValueError:
                issues.append(f"Invalid component type: {comp_type}")
                continue
        component_types.add(comp_type)
    
    if len(component_types) > 1:
        has_button = ComponentType.BUTTON in component_types
        has_select = any(t in component_types for t in [
            ComponentType.STRING_SELECT, ComponentType.USER_SELECT,
            ComponentType.ROLE_SELECT, ComponentType.MENTIONABLE_SELECT,
            ComponentType.CHANNEL_SELECT
        ])
        has_text = ComponentType.TEXT_INPUT in component_types
        
        if has_button and has_select:
            issues.append("Action row cannot mix buttons and select menus")
        if has_text and (has_button or has_select):
            issues.append("Action row cannot mix text inputs with other components")
    
    button_count = sum(1 for c in components if c.get("type") == ComponentType.BUTTON or c.get("type") == 2)
    if button_count > MAX_BUTTONS_PER_ROW:
        issues.append(f"Action row exceeds {MAX_BUTTONS_PER_ROW} buttons")
    
    select_count = sum(1 for c in components if c.get("type") in [3, 5, 6, 7, 8])
    if select_count > MAX_SELECTS_PER_ROW:
        issues.append(f"Action row can only have {MAX_SELECTS_PER_ROW} select menu")
    
    for comp in components:
        comp_type = comp.get("type")
        if comp_type == ComponentType.BUTTON or comp_type == 2:
            valid, comp_issues = validate_button(comp)
            issues.extend(comp_issues)
        elif comp_type in [3, 5, 6, 7, 8]:
            valid, comp_issues = validate_select_menu(comp)
            issues.extend(comp_issues)
        elif comp_type == ComponentType.TEXT_INPUT or comp_type == 4:
            if context != "modal":
                issues.append("Text inputs can only be used in modals")
            else:
                valid, comp_issues = validate_text_input(comp)
                issues.extend(comp_issues)
    
    return len(issues) == 0, issues


def validate_components(components: List[Dict[str, Any]], context: str = "message") -> Tuple[bool, List[str]]:
    """
    Validate a list of components.
    
    Args:
        components: List of component dicts
        context: "message" or "modal"
        
    Returns:
        Tuple of (valid, issues)
    """
    issues = []
    
    max_rows = MAX_MODAL_COMPONENTS if context == "modal" else MAX_ROWS
    if len(components) > max_rows:
        issues.append(f"Exceeds maximum of {max_rows} action rows")
    
    for i, row in enumerate(components):
        row_type = row.get("type")
        if row_type != ComponentType.ACTION_ROW and row_type != 1:
            issues.append(f"Component {i} must be an action row")
            continue
        
        valid, row_issues = validate_action_row(row, context)
        issues.extend(row_issues)
    
    return len(issues) == 0, issues


def components_to_dict(components: List[Any]) -> List[Dict[str, Any]]:
    """Convert components to dict format."""
    result = []
    
    for comp in components:
        if isinstance(comp, ActionRow):
            result.append({
                "type": ComponentType.ACTION_ROW.value,
                "components": components_to_dict(comp.components),
            })
        elif isinstance(comp, Button):
            btn = {
                "type": ComponentType.BUTTON.value,
                "style": comp.style.value if isinstance(comp.style, ButtonStyle) else comp.style,
                "disabled": comp.disabled,
            }
            if comp.label:
                btn["label"] = comp.label
            if comp.emoji:
                btn["emoji"] = comp.emoji
            if comp.custom_id:
                btn["custom_id"] = comp.custom_id
            if comp.url:
                btn["url"] = comp.url
            result.append(btn)
        elif isinstance(comp, SelectMenu):
            sel = {
                "type": comp.component_type.value if isinstance(comp.component_type, ComponentType) else comp.component_type,
                "custom_id": comp.custom_id,
                "min_values": comp.min_values,
                "max_values": comp.max_values,
                "disabled": comp.disabled,
            }
            if comp.options:
                sel["options"] = [
                    {
                        "label": opt.label,
                        "value": opt.value,
                        "description": opt.description,
                        "emoji": opt.emoji,
                        "default": opt.default,
                    }
                    for opt in comp.options
                ]
            if comp.placeholder:
                sel["placeholder"] = comp.placeholder
            if comp.channel_types:
                sel["channel_types"] = comp.channel_types
            result.append(sel)
        elif isinstance(comp, TextInput):
            inp = {
                "type": ComponentType.TEXT_INPUT.value,
                "custom_id": comp.custom_id,
                "style": comp.style.value if isinstance(comp.style, TextInputStyle) else comp.style,
                "label": comp.label,
                "required": comp.required,
            }
            if comp.min_length is not None:
                inp["min_length"] = comp.min_length
            if comp.max_length is not None:
                inp["max_length"] = comp.max_length
            if comp.value:
                inp["value"] = comp.value
            if comp.placeholder:
                inp["placeholder"] = comp.placeholder
            result.append(inp)
        elif isinstance(comp, dict):
            result.append(comp)
    
    return result
