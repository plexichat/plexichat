"""Tests for application components (buttons, select menus, text inputs)."""

import pytest

from src.core.applications.models import (
    ButtonStyle,
    ComponentType,
    TextInputStyle,
    Button,
    SelectMenu,
    SelectOption,
    TextInput,
    ActionRow,
    Modal,
)


@pytest.mark.applications
class TestComponents:
    """Tests for interaction component models."""

    def test_button_styles(self):
        """Test all button styles exist."""
        assert ButtonStyle.PRIMARY.value == 1
        assert ButtonStyle.SECONDARY.value == 2
        assert ButtonStyle.SUCCESS.value == 3
        assert ButtonStyle.DANGER.value == 4
        assert ButtonStyle.LINK.value == 5

    def test_button_creation(self):
        """Test creating a button component."""
        btn = Button(
            style=ButtonStyle.PRIMARY,
            label="Click Me",
            custom_id="btn_click",
        )
        assert btn.style == ButtonStyle.PRIMARY
        assert btn.label == "Click Me"
        assert btn.custom_id == "btn_click"
        assert btn.disabled is False

    def test_link_button(self):
        """Test creating a link button."""
        btn = Button(
            style=ButtonStyle.LINK,
            label="Visit",
            url="https://example.com",
        )
        assert btn.style == ButtonStyle.LINK
        assert btn.url == "https://example.com"

    def test_disabled_button(self):
        """Test creating a disabled button."""
        btn = Button(
            style=ButtonStyle.SECONDARY,
            label="Disabled",
            custom_id="btn_disabled",
            disabled=True,
        )
        assert btn.disabled is True

    def test_component_types(self):
        """Test all component types exist."""
        assert ComponentType.ACTION_ROW.value == 1
        assert ComponentType.BUTTON.value == 2
        assert ComponentType.STRING_SELECT.value == 3
        assert ComponentType.TEXT_INPUT.value == 4
        assert ComponentType.USER_SELECT.value == 5

    def test_select_menu_creation(self):
        """Test creating a select menu component."""
        menu = SelectMenu(
            custom_id="sel_color",
            component_type=ComponentType.STRING_SELECT,
            options=[
                SelectOption(label="Red", value="red"),
                SelectOption(label="Blue", value="blue"),
            ],
            placeholder="Choose a color",
        )
        assert menu.custom_id == "sel_color"
        assert len(menu.options) == 2
        assert menu.placeholder == "Choose a color"

    def test_select_menu_min_max_values(self):
        """Test select menu with custom min/max values."""
        menu = SelectMenu(
            custom_id="sel_multi",
            component_type=ComponentType.STRING_SELECT,
            min_values=1,
            max_values=3,
        )
        assert menu.min_values == 1
        assert menu.max_values == 3

    def test_text_input_creation(self):
        """Test creating a text input component."""
        inp = TextInput(
            custom_id="inp_name",
            style=TextInputStyle.SHORT,
            label="Your Name",
        )
        assert inp.style == TextInputStyle.SHORT
        assert inp.label == "Your Name"
        assert inp.required is True

    def test_paragraph_text_input(self):
        """Test creating a paragraph text input."""
        inp = TextInput(
            custom_id="inp_bio",
            style=TextInputStyle.PARAGRAPH,
            label="Bio",
            min_length=10,
            max_length=500,
            required=False,
        )
        assert inp.style == TextInputStyle.PARAGRAPH
        assert inp.min_length == 10
        assert inp.max_length == 500
        assert inp.required is False

    def test_action_row_creation(self):
        """Test creating an action row with components."""
        row = ActionRow(
            components=[
                Button(style=ButtonStyle.PRIMARY, label="OK", custom_id="ok"),
                Button(style=ButtonStyle.DANGER, label="Cancel", custom_id="cancel"),
            ]
        )
        assert len(row.components) == 2

    def test_modal_creation(self):
        """Test creating a modal dialog."""
        modal = Modal(
            custom_id="modal_feedback",
            title="Feedback",
            components=[
                ActionRow(
                    components=[
                        TextInput(
                            custom_id="inp_msg",
                            style=TextInputStyle.PARAGRAPH,
                            label="Message",
                        )
                    ]
                )
            ],
        )
        assert modal.title == "Feedback"
        assert len(modal.components) == 1
