"""
Regression coverage for the artifact export pipeline.

The export helpers must normalize every historical payload shape (Scribe's
serialized-string ``document`` vs. the launcher template's inline
``document.content_html`` dict, PlexiScript's ``source`` JSON, and the
whiteboard's ``board`` JSON) into readable text/HTML instead of leaking raw
JSON or blank output.
"""

import json

from src.api.routes.artifacts import (
    _artifact_board_text,
    _artifact_export_html,
    _artifact_export_markdown,
    _artifact_export_pdf,
    _artifact_export_text,
    _artifact_native_bytes,
    _artifact_scribe_html,
    _artifact_script_text,
    _latin1_safe,
    _strip_html_markup,
)


class _Artifact:
    title = "Doc"
    summary = "summary"

    class _Type:
        value = "plexiscribe"

    artifact_type = _Type()


def test_scribe_html_from_template_shape():
    payload = {"document": {"content_html": "<h1>Hi</h1><p>Body</p>"}}
    assert _artifact_scribe_html(payload) == "<h1>Hi</h1><p>Body</p>"


def test_scribe_html_from_serialized_editor_shape():
    serialized = json.dumps(
        {"document": {"content_html": "<p>Edited</p>", "content_xml": "<document/>"}}
    )
    payload = {"document": serialized}
    assert _artifact_scribe_html(payload) == "<p>Edited</p>"


def test_scribe_html_from_nested_dict_shape():
    payload = {"document": {"document": {"content_html": "<p>Nested</p>"}}}
    assert _artifact_scribe_html(payload) == "<p>Nested</p>"


def test_scribe_text_strips_html():
    payload = {"document": {"content_html": "<h1>Hi</h1><p>Body &amp; soul</p>"}}
    text = _artifact_export_text("plexiscribe", payload, "summary")
    assert "Hi" in text
    assert "Body & soul" in text
    assert "<h1>" not in text


def test_script_text_prefers_content_then_source():
    assert (
        _artifact_script_text({"content": "console.log(1)", "source": "x"})
        == "console.log(1)"
    )
    assert (
        _artifact_script_text({"source": json.dumps({"content": "from source"})})
        == "from source"
    )
    assert _artifact_script_text({"source": "raw source"}) == "raw source"


def test_board_text_extracts_elements():
    board = json.dumps(
        {
            "text_elements": [{"text": "hello"}],
            "shapes": [{"label": "Box"}],
            "frames": [{"name": "Section 1"}],
        }
    )
    text = _artifact_board_text({"board": board})
    assert "hello" in text
    assert "Box" in text
    assert "Section 1" in text


def test_export_html_embeds_scribe_content_not_json():
    payload = {"document": {"content_html": "<p>Real content</p>"}}
    html = _artifact_export_html(_Artifact(), "plexiscribe", payload)
    assert "<p>Real content</p>" in html
    assert "content_html" not in html
    assert "<!DOCTYPE html>" in html


def test_export_markdown_fences_script():
    artifact = _Artifact()
    artifact.artifact_type.value = "plexiscript"
    md = _artifact_export_markdown(artifact, "let x = 1;")
    assert "# Doc" in md
    assert "```\nlet x = 1;\n```" in md


def test_native_bytes_handles_dict_and_none():
    assert _artifact_native_bytes(None) == b"{}"
    assert json.loads(_artifact_native_bytes({"a": 1})) == {"a": 1}


def test_pdf_export_is_unicode_safe():
    # Non-latin1 titles must not raise (FPDF core fonts are latin-1 only).
    data = _artifact_export_pdf(_Artifact(), "line one\nline two")
    assert isinstance(data, bytes) and data.startswith(b"%PDF")
    # The latin-1 sanitizer replaces (does not raise on) unsupported chars.
    assert _latin1_safe("caf\u00e9 \u4e2d\u6587") == "caf\u00e9 ??"


def test_strip_html_markup_decodes_entities():
    assert _strip_html_markup("<p>a &amp; b</p>") == "a & b"
