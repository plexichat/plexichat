"""Regression tests for interaction component URL and metadata hardening.

Two core-boundary weaknesses are covered:

* Link buttons are rendered as clickable anchors in clients. ``validate_button``
  only checked that a URL existed, so a bot could supply a ``javascript:`` (or
  ``data:``/``file:``) URI and execute script in a user's client on click.
* Select-option label/value/description strings had no length ceiling, so a
  single bot could inflate interaction payloads without bound.

Both validators are reached through :func:`validate_components`, which is the
gate used by the interaction response path.
"""

import pytest

from src.core.applications.interactions.components import (
    MAX_BUTTON_URL_LENGTH,
    MAX_SELECT_OPTION_LABEL_LENGTH,
    MAX_SELECT_OPTION_VALUE_LENGTH,
    MAX_SELECT_OPTION_DESCRIPTION_LENGTH,
    validate_button,
    validate_components,
    validate_select_menu,
)


class TestLinkButtonUrlScheme:
    @pytest.mark.parametrize(
        "bad_url",
        [
            "javascript:alert(1)",
            "JavaScript:alert(document.cookie)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "file:///etc/passwd",
            "vbscript:msgbox(1)",
            "//evil.example.com/path",
            "/relative/path",
            "https://user:pass@example.com/",
            "https://example.com/\r\nSet-Cookie: a=b",
            "https://example.com/\u2028alert(1)",
        ],
    )
    def test_executable_or_unsafe_link_urls_rejected(self, bad_url):
        valid, issues = validate_button(
            {"type": 2, "style": 5, "label": "Visit", "url": bad_url}
        )
        assert not valid
        assert any("URL" in issue for issue in issues), issues

    @pytest.mark.parametrize(
        "good_url",
        [
            "https://example.com",
            "https://example.com/path?x=1#frag",
            "http://example.com/callback",
        ],
    )
    def test_valid_http_s_link_urls_accepted(self, good_url):
        valid, issues = validate_button(
            {"type": 2, "style": 5, "label": "Visit", "url": good_url}
        )
        assert valid, issues

    def test_overlong_link_url_rejected(self):
        url = "https://example.com/" + "a" * (MAX_BUTTON_URL_LENGTH + 1)
        valid, issues = validate_button(
            {"type": 2, "style": 5, "label": "Visit", "url": url}
        )
        assert not valid
        assert any("exceeds" in issue for issue in issues), issues

    def test_non_link_button_still_rejects_a_url(self):
        valid, issues = validate_button(
            {
                "type": 2,
                "style": 1,
                "label": "OK",
                "custom_id": "ok",
                "url": "https://x.example",
            }
        )
        assert not valid
        assert any("cannot have a URL" in issue for issue in issues), issues

    def test_validate_components_gate_rejects_javascript_link_button(self):
        """The interaction-response gate must reject the unsafe URL end to end."""
        components = [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Free coins",
                        "url": "javascript:alert(1)",
                    }
                ],
            }
        ]
        valid, issues = validate_components(components, "message")
        assert not valid
        assert any("URL" in issue for issue in issues), issues

    def test_validate_components_gate_accepts_safe_link_button(self):
        components = [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "label": "Docs",
                        "url": "https://example.com/docs",
                    }
                ],
            }
        ]
        valid, issues = validate_components(components, "message")
        assert valid, issues


class TestSelectOptionMetadataBounds:
    def _select(self, **option):
        return {
            "type": 3,
            "custom_id": "sel",
            "options": [{"label": "Red", "value": "red", **option}],
        }

    def test_overlong_option_fields_rejected(self):
        cases = [
            ("label", "l" * (MAX_SELECT_OPTION_LABEL_LENGTH + 1)),
            ("value", "v" * (MAX_SELECT_OPTION_VALUE_LENGTH + 1)),
            ("description", "d" * (MAX_SELECT_OPTION_DESCRIPTION_LENGTH + 1)),
        ]
        for field, oversized in cases:
            valid, issues = validate_select_menu(self._select(**{field: oversized}))
            assert not valid, field
            assert any(field in issue for issue in issues), (field, issues)

    def test_bounded_option_fields_accepted(self):
        valid, issues = validate_select_menu(
            self._select(
                label="l" * MAX_SELECT_OPTION_LABEL_LENGTH,
                value="v" * MAX_SELECT_OPTION_VALUE_LENGTH,
                description="d" * MAX_SELECT_OPTION_DESCRIPTION_LENGTH,
            )
        )
        assert valid, issues

    def test_validate_components_gate_rejects_overlong_option(self):
        components = [
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": "sel",
                        "options": [
                            {
                                "label": "x" * (MAX_SELECT_OPTION_LABEL_LENGTH + 1),
                                "value": "v",
                            }
                        ],
                    }
                ],
            }
        ]
        valid, issues = validate_components(components, "message")
        assert not valid
        assert any("label exceeds" in issue for issue in issues), issues
