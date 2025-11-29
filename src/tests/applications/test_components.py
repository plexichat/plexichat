"""
Tests for component builders and validation.
"""

import pytest

from src.core.applications import (
    ButtonStyle,
    ComponentType,
    TextInputStyle,
    build_button,
    build_select_menu,
    build_text_input,
    build_action_row,
    build_modal,
    validate_components,
)


@pytest.mark.applications
@pytest.mark.integration
class TestButtonBuilder:
    """Tests for button component builder."""

    def test_build_primary_button(self):
        """Test building a primary button."""
        btn = build_button(
            style=ButtonStyle.PRIMARY,
            label="Click Me",
            custom_id="btn_click",
        )

        assert btn.style == ButtonStyle.PRIMARY
        assert btn.label == "Click Me"
        assert btn.custom_id == "btn_click"
        assert btn.disabled is False

    def test_build_secondary_button(self):
        """Test building a secondary button."""
        btn = build_button(
            style=ButtonStyle.SECONDARY,
            label="Cancel",
            custom_id="btn_cancel",
        )

        assert btn.style == ButtonStyle.SECONDARY

    def test_build_success_button(self):
        """Test building a success button."""
        btn = build_button(
            style=ButtonStyle.SUCCESS,
            label="Confirm",
            custom_id="btn_confirm",
        )

        assert btn.style == ButtonStyle.SUCCESS

    def test_build_danger_button(self):
        """Test building a danger button."""
        btn = build_button(
            style=ButtonStyle.DANGER,
            label="Delete",
            custom_id="btn_delete",
        )

        assert btn.style == ButtonStyle.DANGER

    def test_build_link_button(self):
        """Test building a link button."""
        btn = build_button(
            style=ButtonStyle.LINK,
            label="Visit Website",
            url="https://example.com",
        )

        assert btn.style == ButtonStyle.LINK
        assert btn.url == "https://example.com"
        assert btn.custom_id is None

    def test_build_button_with_emoji(self):
        """Test building button with emoji."""
        btn = build_button(
            style=ButtonStyle.PRIMARY,
            label="Like",
            custom_id="btn_like",
            emoji={"name": "thumbsup"},
        )

        assert btn.emoji == {"name": "thumbsup"}

    def test_build_disabled_button(self):
        """Test building disabled button."""
        btn = build_button(
            style=ButtonStyle.PRIMARY,
            label="Disabled",
            custom_id="btn_disabled",
            disabled=True,
        )

        assert btn.disabled is True


@pytest.mark.applications
@pytest.mark.integration
class TestSelectMenuBuilder:
    """Tests for select menu component builder."""

    def test_build_string_select(self):
        """Test building a string select menu."""
        select = build_select_menu(
            custom_id="color_select",
            component_type=ComponentType.STRING_SELECT,
            options=[
                {"label": "Red", "value": "red"},
                {"label": "Green", "value": "green"},
                {"label": "Blue", "value": "blue"},
            ],
        )

        assert select.custom_id == "color_select"
        assert select.component_type == ComponentType.STRING_SELECT
        assert len(select.options) == 3

    def test_build_select_with_placeholder(self):
        """Test building select with placeholder."""
        select = build_select_menu(
            custom_id="test_select",
            options=[{"label": "Option", "value": "opt"}],
            placeholder="Choose an option",
        )

        assert select.placeholder == "Choose an option"

    def test_build_select_with_min_max(self):
        """Test building select with min/max values."""
        select = build_select_menu(
            custom_id="multi_select",
            options=[
                {"label": "A", "value": "a"},
                {"label": "B", "value": "b"},
                {"label": "C", "value": "c"},
            ],
            min_values=1,
            max_values=3,
        )

        assert select.min_values == 1
        assert select.max_values == 3

    def test_build_user_select(self):
        """Test building user select menu."""
        select = build_select_menu(
            custom_id="user_select",
            component_type=ComponentType.USER_SELECT,
        )

        assert select.component_type == ComponentType.USER_SELECT

    def test_build_role_select(self):
        """Test building role select menu."""
        select = build_select_menu(
            custom_id="role_select",
            component_type=ComponentType.ROLE_SELECT,
        )

        assert select.component_type == ComponentType.ROLE_SELECT

    def test_build_channel_select(self):
        """Test building channel select menu."""
        select = build_select_menu(
            custom_id="channel_select",
            component_type=ComponentType.CHANNEL_SELECT,
            channel_types=[0, 2],
        )

        assert select.component_type == ComponentType.CHANNEL_SELECT
        assert select.channel_types == [0, 2]


@pytest.mark.applications
@pytest.mark.integration
class TestTextInputBuilder:
    """Tests for text input component builder."""

    def test_build_short_text_input(self):
        """Test building short text input."""
        inp = build_text_input(
            custom_id="name_input",
            label="Your Name",
            style=TextInputStyle.SHORT,
        )

        assert inp.custom_id == "name_input"
        assert inp.label == "Your Name"
        assert inp.style == TextInputStyle.SHORT

    def test_build_paragraph_text_input(self):
        """Test building paragraph text input."""
        inp = build_text_input(
            custom_id="bio_input",
            label="Your Bio",
            style=TextInputStyle.PARAGRAPH,
        )

        assert inp.style == TextInputStyle.PARAGRAPH

    def test_build_text_input_with_constraints(self):
        """Test building text input with length constraints."""
        inp = build_text_input(
            custom_id="code_input",
            label="Code",
            min_length=10,
            max_length=100,
        )

        assert inp.min_length == 10
        assert inp.max_length == 100

    def test_build_optional_text_input(self):
        """Test building optional text input."""
        inp = build_text_input(
            custom_id="optional_input",
            label="Optional Field",
            required=False,
        )

        assert inp.required is False

    def test_build_text_input_with_default(self):
        """Test building text input with default value."""
        inp = build_text_input(
            custom_id="prefilled_input",
            label="Prefilled",
            value="Default value",
        )

        assert inp.value == "Default value"

    def test_build_text_input_with_placeholder(self):
        """Test building text input with placeholder."""
        inp = build_text_input(
            custom_id="placeholder_input",
            label="Input",
            placeholder="Enter something...",
        )

        assert inp.placeholder == "Enter something..."


@pytest.mark.applications
@pytest.mark.integration
class TestActionRowBuilder:
    """Tests for action row builder."""

    def test_build_action_row_with_buttons(self):
        """Test building action row with buttons."""
        btn1 = build_button(ButtonStyle.PRIMARY, "Button 1", custom_id="btn1")
        btn2 = build_button(ButtonStyle.SECONDARY, "Button 2", custom_id="btn2")

        row = build_action_row([btn1, btn2])

        assert len(row.components) == 2

    def test_build_action_row_with_select(self):
        """Test building action row with select menu."""
        select = build_select_menu(
            custom_id="select",
            options=[{"label": "Option", "value": "opt"}],
        )

        row = build_action_row([select])

        assert len(row.components) == 1


@pytest.mark.applications
@pytest.mark.integration
class TestModalBuilder:
    """Tests for modal builder."""

    def test_build_modal(self):
        """Test building a modal."""
        inp = build_text_input(
            custom_id="feedback",
            label="Feedback",
            style=TextInputStyle.PARAGRAPH,
        )
        row = build_action_row([inp])

        modal = build_modal(
            custom_id="feedback_modal",
            title="Submit Feedback",
            components=[row],
        )

        assert modal.custom_id == "feedback_modal"
        assert modal.title == "Submit Feedback"
        assert len(modal.components) == 1


@pytest.mark.applications
@pytest.mark.integration
class TestComponentValidation:
    """Tests for component validation."""

    def test_validate_valid_button(self):
        """Test validating valid button."""
        from src.core.applications.interactions.components import validate_button

        valid, issues = validate_button({
            "type": 2,
            "style": 1,
            "label": "Click",
            "custom_id": "btn",
        })

        assert valid is True

    def test_validate_button_missing_label(self):
        """Test validating button without label or emoji."""
        from src.core.applications.interactions.components import validate_button

        valid, issues = validate_button({
            "type": 2,
            "style": 1,
            "custom_id": "btn",
        })

        assert valid is False

    def test_validate_link_button_without_url(self):
        """Test validating link button without URL."""
        from src.core.applications.interactions.components import validate_button

        valid, issues = validate_button({
            "type": 2,
            "style": 5,
            "label": "Link",
        })

        assert valid is False

    def test_validate_valid_select(self):
        """Test validating valid select menu."""
        from src.core.applications.interactions.components import validate_select_menu

        valid, issues = validate_select_menu({
            "type": 3,
            "custom_id": "select",
            "options": [{"label": "Option", "value": "opt"}],
        })

        assert valid is True

    def test_validate_select_without_options(self):
        """Test validating string select without options."""
        from src.core.applications.interactions.components import validate_select_menu

        valid, issues = validate_select_menu({
            "type": 3,
            "custom_id": "select",
        })

        assert valid is False

    def test_validate_components_too_many_rows(self):
        """Test validating too many action rows."""
        components = [
            {"type": 1, "components": [{"type": 2, "style": 1, "label": f"Btn {i}", "custom_id": f"btn{i}"}]}
            for i in range(10)
        ]

        valid, issues = validate_components(components)

        assert valid is False

    def test_validate_components_mixed_types(self):
        """Test validating action row with mixed component types."""
        from src.core.applications.interactions.components import validate_action_row

        valid, issues = validate_action_row({
            "type": 1,
            "components": [
                {"type": 2, "style": 1, "label": "Button", "custom_id": "btn"},
                {"type": 3, "custom_id": "select", "options": [{"label": "Opt", "value": "opt"}]},
            ],
        })

        assert valid is False
