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
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Dict, List, Optional

from fpdf import FPDF
from odf.opendocument import OpenDocumentText
from odf.text import P


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
    """Force text into latin-1 so FPDF core fonts never raise on non-ASCII."""
    return value.encode("latin-1", "replace").decode("latin-1")


def _artifact_export_pdf(artifact: Any, text: str) -> bytes:
    """Render a PDF export using extracted text (latin-1 safe)."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=2.0)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    title = _latin1_safe(artifact.title or "Document")
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    for line in _latin1_safe(text or "").splitlines():
        pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf_bytes = pdf.output()
    return bytes(pdf_bytes) if pdf_bytes else b""


def _artifact_export_odt(artifact: Any, text: str) -> bytes:
    """Render an ODT export using extracted text."""
    doc = OpenDocumentText()
    doc_text = getattr(doc, "text")
    doc_text.addElement(P(text=artifact.title or "Document"))
    doc_text.addElement(P(text=""))
    for line in (text or "").splitlines():
        doc_text.addElement(P(text=line))
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
