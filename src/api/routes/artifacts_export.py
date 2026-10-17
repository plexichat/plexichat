"""
Artifact export renderers - format helpers for the artifact download route.

Extracted from ``routes/artifacts.py`` to keep each module focused (and under
reviewable size); the symbols are re-exported by ``routes/artifacts.py`` so
existing import paths remain stable.

Every renderer treats artifact payloads as USER-AUTHORED content: rich-text
HTML is passed through an allowlist sanitizer before it is embedded in an
export document, and every other branch escapes its content explicitly.
"""

import html as _html
import json
import re
import zipfile
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Dict, List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


def _json_loads(value: Any) -> Any:
    """Parse a JSON string without raising; returns any other value unchanged."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


# === HTML sanitization (defense against stored XSS via payload content) ====


# Allowlisted tags for exported rich text. Everything else is dropped (its
# text content is preserved); script/style contents are removed entirely.
_ALLOWED_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "caption",
        "code",
        "col",
        "colgroup",
        "dd",
        "del",
        "div",
        "dl",
        "dt",
        "em",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "ins",
        "kbd",
        "li",
        "mark",
        "ol",
        "p",
        "pre",
        "q",
        "s",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)
_VOID_TAGS = frozenset({"br", "col", "hr", "img"})
# Only benign attributes survive; anything that can execute code or change
# the export document's own identity is stripped.
_URL_ATTRIBUTES = {"href": "a", "src": "img"}
_SAFE_URL_SCHEMES = ("http:", "https:", "mailto:")
# Browsers ignore ASCII tab/newline/CR (and other C0 controls) inside URLs, so
# "java\tscript:alert(1)" executes as "javascript:alert(1)". Strip EVERY C0
# control, DEL and space character before scheme checks so the scheme check
# sees exactly what the browser will see.
_URL_JUNK_RE = re.compile(r"[\x00-\x20\x7f]")


def _safe_url(value: str) -> Optional[str]:
    """Return ``value`` when it is a benign absolute/relative URL."""
    candidate = _URL_JUNK_RE.sub("", value or "")
    lowered = candidate.lower()
    if lowered.startswith(("javascript:", "vbscript:", "data:", "file:")):
        return None
    if (
        "://" not in candidate
        and not lowered.startswith("mailto:")
        and not candidate.startswith("#")
    ):
        # Relative URL - fine inside a standalone document.
        return candidate
    for scheme in _SAFE_URL_SCHEMES:
        if lowered.startswith(scheme):
            return candidate
    return None


class _AllowlistHTMLSanitizer(HTMLParser):
    """Rebuild an HTML fragment keeping only allowlisted tags/attributes.

    Text outside allowed tags is preserved (escaped); text inside
    ``script``/``style``/``iframe`` bodies is dropped entirely; event-handler
    and style attributes never survive.
    """

    _DROP_CONTENT_TAGS = frozenset(
        {"script", "style", "iframe", "object", "embed", "noscript", "template"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: List[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:
        tag = tag.lower()
        if tag in self._DROP_CONTENT_TAGS:
            if tag not in _VOID_TAGS:
                self._drop_depth += 1
            return
        if self._drop_depth:
            return
        if tag not in _ALLOWED_TAGS:
            return
        parts = [tag]
        for name, value in attrs:
            if not isinstance(name, str):
                continue
            name = name.lower()
            if name.startswith("on") or name in ("style", "formaction"):
                continue
            if value is None:
                continue
            str_value = str(value)
            if name in _URL_ATTRIBUTES:
                safe = _safe_url(str_value)
                if safe is None:
                    continue
                str_value = safe
            parts.append(f'{name}="{_html.escape(str_value, quote=True)}"')
        rendered = "<" + " ".join(parts) + ">"
        if tag in _VOID_TAGS:
            rendered = rendered[:-1] + "/>"
        self._out.append(rendered)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._DROP_CONTENT_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if self._drop_depth:
            return
        if tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._drop_depth or not data:
            return
        self._out.append(_html.escape(data, quote=False))

    def result(self) -> str:
        return "".join(self._out)


def sanitize_rich_html(value: str) -> str:
    """Sanitize user-authored rich text down to a safe allowlisted subset.

    Used wherever plexiscribe ``content_html`` is embedded verbatim (e.g. the
    HTML export): scripts, event handlers, style attributes and dangerous URLs
    never survive, while ordinary formatting markup and its escaped text do.
    """
    if not isinstance(value, str) or not value:
        return ""
    try:
        parser = _AllowlistHTMLSanitizer()
        parser.feed(value)
        parser.close()
    except Exception:
        # Fail closed: if the fragment cannot be parsed cleanly it is reduced
        # to fully-escaped plain text instead of being embedded raw.
        return _html.escape(value, quote=False)
    return parser.result()


# === Payload shape normalization ===========================================


def _artifact_scribe_html(payload: Dict[str, Any]) -> str:
    """Return the content HTML of a plexiscribe payload across stored shapes.

    The editor historically stored ``payload.document`` as a serialized JSON
    string while the launcher templates stored it as a dict with a direct
    ``content_html`` key. Both (and a nested ``document.document`` shape) are
    normalized here so exports and text extraction never return raw JSON.
    """
    doc = _json_loads(payload.get("document"))
    if isinstance(doc, dict):
        if isinstance(doc.get("content_html"), str):
            return doc["content_html"]
        inner = doc.get("document")
        if isinstance(inner, dict) and isinstance(inner.get("content_html"), str):
            return inner["content_html"]
    top = payload.get("content_html")
    if isinstance(top, str):
        return top
    return ""


def _artifact_script_text(payload: Dict[str, Any]) -> str:
    """Return the readable source of a plexiscript payload."""
    content = payload.get("content")
    if isinstance(content, str) and content:
        return content
    source = _json_loads(payload.get("source"))
    if isinstance(source, dict):
        for key in ("content", "source", "text"):
            if isinstance(source.get(key), str):
                return source[key]
    if isinstance(source, str):
        return source
    return ""


def _artifact_board_text(payload: Dict[str, Any]) -> str:
    """Return readable text from a whiteboard payload's ``board`` JSON."""
    board = _json_loads(payload.get("board"))
    if not isinstance(board, dict):
        return ""
    parts: List[str] = []
    for element in board.get("text_elements") or []:
        if isinstance(element, dict) and element.get("text"):
            parts.append(str(element["text"]))
    for shape in board.get("shapes") or []:
        if isinstance(shape, dict):
            for key in ("label", "text", "title"):
                if shape.get(key):
                    parts.append(str(shape[key]))
                    break
    for frame in board.get("frames") or []:
        if isinstance(frame, dict) and frame.get("name"):
            parts.append(str(frame["name"]))
    return "\n".join(parts)


def _strip_html_markup(value: str) -> str:
    """Convert HTML to plain text (tags removed, entities decoded)."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = _html.unescape(text).replace("\xa0", " ")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _artifact_export_text(
    artifact_type_value: str, payload: Dict[str, Any], summary: Optional[str]
) -> str:
    """Extract human-readable text for any artifact payload shape."""
    if artifact_type_value == "plexiscribe":
        return _strip_html_markup(_artifact_scribe_html(payload)) or (summary or "")
    if artifact_type_value == "plexiscript":
        return _artifact_script_text(payload)
    if artifact_type_value == "whiteboard":
        return _artifact_board_text(payload) or (summary or "")
    return str(payload.get("content") or payload.get("text") or summary or "")


def _artifact_native_bytes(value: Any) -> bytes:
    """Serialize a native-format export value that may be str, dict, or None."""
    if isinstance(value, str):
        return value.encode("utf-8", "replace")
    if value is None:
        return b"{}"
    return json.dumps(value, ensure_ascii=False).encode("utf-8", "replace")


# === Export document renderers =============================================


def _artifact_export_html(
    artifact: Any, artifact_type_value: str, payload: Dict[str, Any]
) -> str:
    """Render a standalone HTML export document."""
    if artifact_type_value == "plexiscribe":
        # Rich text is user-authored: sanitize it through the allowlist so
        # scripts/handlers/dangerous URLs cannot ship inside the download.
        body = sanitize_rich_html(_artifact_scribe_html(payload))
    elif artifact_type_value == "plexiscript":
        body = "<pre>" + _html.escape(_artifact_script_text(payload)) + "</pre>"
    elif artifact_type_value == "whiteboard":
        body = "<pre>" + _html.escape(_artifact_board_text(payload)) + "</pre>"
    else:
        body = _html.escape(str(payload.get("content") or payload.get("text") or ""))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{_html.escape(artifact.title or '')}</title></head>"
        f"<body><h1>{_html.escape(artifact.title or '')}</h1>{body}</body></html>"
    )


def _artifact_export_markdown(artifact: Any, text: str) -> str:
    """Render a Markdown export document."""
    title = _html.escape(artifact.title or "")
    summary = _html.escape(artifact.summary or "")
    if artifact.artifact_type.value == "plexiscript":
        body = "```\n" + text + "\n```"
    else:
        body = text
    return f"# {title}\n\n{summary}\n\n{body}\n"


def _latin1_safe(value: str) -> str:
    """Force text into latin-1 so PDF core fonts never raise on non-ASCII."""
    return value.encode("latin-1", "replace").decode("latin-1")


# PDF layout constants (points). ``reportlab`` is used rather than fpdf2: fpdf2
# is LGPL-3.0-only and these renderers ship inside distributed builds.
_PDF_MARGIN = 40.0
_PDF_TITLE_SIZE = 15.0
_PDF_BODY_SIZE = 10.0
_PDF_FONT = "Helvetica"


def _pdf_wrap(text: str, size: float, max_width: float) -> List[str]:
    """Greedy word wrap at the right margin, preserving blank lines.

    A word wider than the text area (a long URL or an unbroken token) is
    hard-split, so no line can run off the page.
    """
    lines: List[str] = []
    for paragraph in (text or "").split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = word if not current else f"{current} {word}"
            if stringWidth(candidate, _PDF_FONT, size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            while stringWidth(word, _PDF_FONT, size) > max_width and len(word) > 1:
                cut = len(word)
                while cut > 1 and stringWidth(word[:cut], _PDF_FONT, size) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


def _pdf_draw_lines(pdf: Any, lines: List[str], y: float, size: float) -> float:
    """Draw wrapped lines, paginating when the bottom margin is reached.

    Returns the cursor position after the last line so a following block (the
    body, after a wrapped title) continues underneath it instead of overlapping.
    """
    page_height = A4[1]
    line_height = size + 3.0

    for line in lines:
        if y <= _PDF_MARGIN:
            pdf.showPage()
            pdf.setFont(_PDF_FONT, size)
            y = page_height - _PDF_MARGIN
        if line:
            pdf.drawString(_PDF_MARGIN, y, line[:4096])
        y -= line_height
    return y


def _artifact_export_pdf(artifact: Any, text: str) -> bytes:
    """Render a PDF export using extracted text (latin-1 safe)."""
    page_width, page_height = A4
    max_width = page_width - (2 * _PDF_MARGIN)

    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setTitle(_latin1_safe(artifact.title or "Document")[:200])

    y = page_height - _PDF_MARGIN
    pdf.setFont(_PDF_FONT, _PDF_TITLE_SIZE)
    y = _pdf_draw_lines(
        pdf,
        _pdf_wrap(
            _latin1_safe(artifact.title or "Document"), _PDF_TITLE_SIZE, max_width
        ),
        y,
        _PDF_TITLE_SIZE,
    )

    pdf.setFont(_PDF_FONT, _PDF_BODY_SIZE)
    _pdf_draw_lines(
        pdf,
        _pdf_wrap(_latin1_safe(text or ""), _PDF_BODY_SIZE, max_width),
        y - 6.0,
        _PDF_BODY_SIZE,
    )

    pdf.save()
    return buf.getvalue()


# === ODT rendering (hand-rolled: odfpy is GPL/LGPL-flagged) ================
#
# An ODF text document is a ZIP containing a stored ``mimetype`` entry, a
# manifest, and XML parts. The export only emits paragraphs, so a dependency
# that ships GPL/LGPL metadata buys nothing here.

_ODF_MIMETYPE = "application/vnd.oasis.opendocument.text"

# Fixed timestamp: keeps exported bytes reproducible instead of embedding the
# export time into the archive headers.
_ODF_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# XML 1.0 forbids these control characters outright, even escaped.
_ODF_INVALID_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_ODF_MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<manifest:manifest "
    'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
    'manifest:version="1.2">'
    '<manifest:file-entry manifest:full-path="/" '
    f'manifest:media-type="{_ODF_MIMETYPE}"/>'
    '<manifest:file-entry manifest:full-path="content.xml" '
    'manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="styles.xml" '
    'manifest:media-type="text/xml"/>'
    '<manifest:file-entry manifest:full-path="meta.xml" '
    'manifest:media-type="text/xml"/>'
    "</manifest:manifest>"
)

_ODF_STYLES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<office:document-styles "
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'office:version="1.2">'
    "<office:styles>"
    '<style:default-style style:family="paragraph">'
    '<style:paragraph-properties fo:orphans="2" fo:widows="2"/>'
    "</style:default-style>"
    "</office:styles>"
    "</office:document-styles>"
)


def _odf_text(value: str) -> str:
    """Escape text for XML and drop characters XML 1.0 cannot represent."""
    return _html.escape(_ODF_INVALID_CHARS.sub("", value or ""), quote=False)


def _odf_content_xml(title: str, lines: List[str]) -> str:
    """Build the document body: a bold title paragraph, then the text."""
    paragraphs = [
        '<text:p text:style-name="ExportTitle">'
        f"{_odf_text(title) or 'Document'}</text:p>",
        "<text:p/>",
    ]
    paragraphs.extend(f"<text:p>{_odf_text(line)}</text:p>" for line in lines)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<office:document-content "
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
        'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
        'office:version="1.2">'
        "<office:automatic-styles>"
        '<style:style style:name="ExportTitle" style:family="paragraph">'
        '<style:text-properties fo:font-size="16pt" fo:font-weight="bold"/>'
        "</style:style>"
        "</office:automatic-styles>"
        "<office:body><office:text>"
        + "".join(paragraphs)
        + "</office:text></office:body>"
        "</office:document-content>"
    )


def _odf_meta_xml(title: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<office:document-meta "
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'office:version="1.2">'
        "<office:meta>"
        f"<dc:title>{_odf_text(title) or 'Document'}</dc:title>"
        '<meta:generator xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0">'
        "Plexichat</meta:generator>"
        "</office:meta>"
        "</office:document-meta>"
    )


def _artifact_export_odt(artifact: Any, text: str) -> bytes:
    """Render an ODT export using extracted text."""
    title = artifact.title or "Document"
    lines = (text or "").split("\n")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        # ODF requires ``mimetype`` to be the first entry and stored uncompressed
        # so consumers can identify the file without reading the whole archive.
        mimetype = zipfile.ZipInfo("mimetype", date_time=_ODF_ZIP_EPOCH)
        archive.writestr(mimetype, _ODF_MIMETYPE, compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/manifest.xml", _ODF_MANIFEST)
        archive.writestr("content.xml", _odf_content_xml(title, lines))
        archive.writestr("styles.xml", _ODF_STYLES)
        archive.writestr("meta.xml", _odf_meta_xml(title))
    return buf.getvalue()
