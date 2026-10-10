"""
Regression coverage for the artifact export pipeline.

The export helpers must normalize every historical payload shape (Scribe's
serialized-string ``document`` vs. the launcher template's inline
``document.content_html`` dict, PlexiScript's ``source`` JSON, and the
whiteboard's ``board`` JSON) into readable text/HTML instead of leaking raw
JSON or blank output.
"""

import io
import json
import zipfile
from xml.etree import ElementTree

from src.api.routes.artifacts import (
    _artifact_board_text,
    _artifact_export_html,
    _artifact_export_markdown,
    _artifact_export_odt,
    _artifact_export_pdf,
    _artifact_export_text,
    _artifact_native_bytes,
    _artifact_scribe_html,
    _artifact_script_text,
    _latin1_safe,
    _strip_html_markup,
    sanitize_rich_html,
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


# --- Document renderers ship with permissive dependencies ------------------
#
# PDF uses reportlab (BSD-3-Clause) and ODT is written directly (a zip of XML),
# because fpdf2 (LGPL-3.0-only) and odfpy (GPL/LGPL-flagged metadata) must not
# be bundled into a distributed build.


def _pdf_page_count(data: bytes) -> int:
    """Count page objects, excluding the ``/Pages`` tree node."""
    return data.count(b"/Type /Page") - data.count(b"/Type /Pages")


def test_pdf_export_paginates_long_documents():
    long_text = "\n".join(f"line {i} " + "word " * 20 for i in range(200))
    data = _artifact_export_pdf(_Artifact(), long_text)
    assert data.startswith(b"%PDF")
    assert data.rstrip().endswith(b"%%EOF")
    # More than one page means the pagination path ran.
    assert _pdf_page_count(data) > 1


def test_pdf_export_keeps_a_single_page_for_short_documents():
    data = _artifact_export_pdf(_Artifact(), "just one line")
    assert _pdf_page_count(data) == 1


def test_odt_export_is_a_valid_odf_package():
    data = _artifact_export_odt(_Artifact(), "hello\nworld")
    assert data.startswith(b"PK")  # ZIP container

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        # ODF requires the mimetype entry to come first, stored uncompressed.
        assert names[0] == "mimetype"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/vnd.oasis.opendocument.text"
        assert "META-INF/manifest.xml" in names
        content = archive.read("content.xml").decode("utf-8")

    root = ElementTree.fromstring(content)
    assert root.tag.endswith("document-content")
    assert "Doc" in content
    assert "hello" in content
    assert "world" in content


def test_odt_export_escapes_hostile_text():
    hostile = "</text:p><script>alert(1)</script>& <b>bold</b>"
    data = _artifact_export_odt(_Artifact(), hostile)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        content = archive.read("content.xml").decode("utf-8")

    # The document must still parse, with the markup neutralised as text.
    ElementTree.fromstring(content)
    assert "<script" not in content
    assert "&lt;script&gt;" in content
    assert "&amp;" in content


def test_odt_export_drops_characters_xml_forbids():
    data = _artifact_export_odt(_Artifact(), "before\x00\x07after")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        content = archive.read("content.xml").decode("utf-8")

    ElementTree.fromstring(content)
    assert "beforeafter" in content


# --- Stored-XSS regression coverage (H-24) ---------------------------------


def test_export_html_sanitizes_scribe_script_payload():
    payload = {
        "document": {
            "content_html": (
                "<p>Safe text</p><script>alert('xss')</script>"
                "<img src=x onerror=alert(1)>"
                "<a href=\"javascript:alert('xss')\">link</a>"
            )
        }
    }
    html = _artifact_export_html(_Artifact(), "plexiscribe", payload)
    assert "Safe text" in html
    assert "<script" not in html.lower()
    assert "alert(" not in html
    assert "onerror" not in html.lower()
    assert "javascript:" not in html.lower()


def test_sanitize_rich_html_keeps_allowlisted_formatting():
    dirty = (
        '<div style="color:red"><b>Bold</b> &amp; <i>italic</i>'
        '<a href="https://example.test/x" onclick="evil()">ok</a></div>'
    )
    clean = sanitize_rich_html(dirty)
    assert "<b>Bold</b>" in clean
    assert "&amp;" in clean or "&" in clean
    assert 'href="https://example.test/x"' in clean
    assert "onclick" not in clean
    assert "style=" not in clean


def test_sanitize_rich_html_drops_script_body_and_escapes_loose_text():
    clean = sanitize_rich_html("<script>document.cookie</script>plain <b>text</b>")
    assert "document.cookie" not in clean
    assert "plain" in clean
    assert "<b>text</b>" in clean


def test_sanitize_rich_html_never_raises_on_hostile_input():
    for hostile in ("<p", "<<>", "<a href='", "\x00<script>", ""):
        result = sanitize_rich_html(hostile)
        assert isinstance(result, str)
        assert "<script" not in result.lower()


# --- URL scheme-check control-character stripping (W3 tab/formfeed) --------


def test_safe_url_rejects_tab_and_formfeed_obfuscated_schemes():
    from src.api.routes.artifacts_export import _safe_url

    # Browsers strip ASCII tabs/newlines/controls inside URLs, so these all
    # execute as javascript: in an exported document unless stripped before
    # the scheme check.
    assert _safe_url("java\tscript:alert(1)") is None
    assert _safe_url("java\x0cscript:alert(1)") is None
    assert _safe_url("java\nscript:alert(1)") is None
    assert _safe_url("java\rscript:alert(1)") is None
    assert _safe_url("jav\x00ascript:alert(1)") is None
    assert _safe_url("jav\x7fascript:alert(1)") is None
    assert _safe_url("\x01javascript:alert(1)") is None
    assert _safe_url("vbs\x0bcript:alert(1)") is None


def test_safe_url_still_accepts_benign_urls():
    from src.api.routes.artifacts_export import _safe_url

    assert _safe_url("https://example.test/a?b=c") == "https://example.test/a?b=c"
    assert _safe_url("mailto:user@example.test") == "mailto:user@example.test"
    assert _safe_url("relative/path.html") == "relative/path.html"
    assert _safe_url("file:///etc/passwd") is None
    assert _safe_url("data:text/html;base64,PHNjcmlwdD4=") is None


def test_sanitizer_drops_control_obfuscated_href_entirely():
    dirty = '<a href="java\tscript:alert(1)">click</a>'
    clean = sanitize_rich_html(dirty)
    lowered = clean.lower()
    assert "alert(" not in lowered
    # The dangerous href must not survive in any form.
    assert "script:alert" not in lowered.replace("&#x27;", "").replace("&quot;", "")
