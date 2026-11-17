"""SEC3 invariant: outbound HTTP is gated and every request has a deadline.

A server-side fetch of a URL an attacker can influence is an SSRF primitive,
and a fetch with no timeout is a slowloris / hung-worker primitive. This
suite locks in both halves across the backend:

* the transport floor (``src.utils.outbound.urlopen``) refuses non-http(s)
  URLs and always carries a timeout;
* modules that fetch user-supplied URLs call the shared SSRF validator and
  pass an explicit timeout;
* AST inspection of production source finds no direct ``requests``/``httpx``
  verb call, ``urlopen`` or urllib3 ``pool.urlopen`` without a ``timeout=``
  keyword; and
* no production module reaches for ``urllib.request.urlopen`` directly,
  bypassing the scheme allow-list in the transport floor.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]

HTTP_VERBS = {"get", "post", "put", "delete", "patch", "request", "head", "options"}

# Modules that fetch a URL which ultimately comes from a request body,
# an embed, a webhook registration or a federation peer. Each must both gate
# the destination through the shared validator and bound the request.
GATED_FETCHERS = {
    "src/core/media/security/proxy.py",
    "src/core/embeds/link_preview.py",
    "src/core/artifacts/federation.py",
    "src/core/artifacts/transcription/provider.py",
    "src/core/automod/ai/openai.py",
    "src/core/automod/ai/custom.py",
    "src/core/automod/ai/perspective.py",
    "src/core/automod/ai/shieldstral.py",
    "src/core/search/indexer/meilisearch.py",
    "src/core/search/indexer/elasticsearch.py",
}
GATE_TOKENS = (
    "URLValidator",
    "validate_url_for_request",
    "pinned_request_target",
    "resolve_outbound_url",
    "parse_outbound_url",
    # The transport floor is itself a gate: it refuses non-http(s) schemes.
    "from src.utils.outbound import urlopen",
)


def _production_files():
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if "/tests/" in relative or "/selftest/" in relative or relative.startswith("src/cli/"):
            continue
        yield relative, path


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _bounded_http_calls():
    """Return (all direct HTTP calls, the ones missing a timeout)."""
    all_calls = []
    unbounded = []
    for relative, path in _production_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            root = name.split(".")[0] if "." in name else name
            is_http = (
                root in ("requests", "httpx")
                and name.count(".") == 1
                and name.rsplit(".", 1)[1] in HTTP_VERBS
            ) or name == "urlopen" or name.endswith(".urlopen")
            if not is_http:
                continue
            all_calls.append((relative, node.lineno, name))
            if not any(keyword.arg == "timeout" for keyword in node.keywords):
                unbounded.append((relative, node.lineno, name))
    return all_calls, unbounded


def test_transport_floor_rejects_non_http_schemes_and_has_a_default_deadline():
    from urllib.request import Request

    from src.utils.outbound import urlopen

    assert inspect.signature(urlopen).parameters["timeout"].default > 0
    for url in ("file:///etc/passwd", "ftp://example.com/x", "/relative"):
        with pytest.raises(ValueError):
            urlopen(Request(url))


def test_pinned_request_target_keeps_the_hostname_and_uses_the_vetted_ip():
    from src.utils.security import pinned_request_target

    url, headers, extensions = pinned_request_target(
        "https://example.com/invite", "example.com", "203.0.113.9"
    )
    assert "203.0.113.9" in url
    assert url.startswith("https://")
    assert headers["Host"] == "example.com"
    assert extensions["sni_hostname"] == "example.com"


def test_shared_gate_rejects_a_non_http_scheme_without_resolving():
    from src.core.security.errors import OutboundUrlRejected
    from src.core.security.outbound import parse_outbound_url

    with patch("src.core.security.outbound.URLValidator") as validator:
        with pytest.raises(OutboundUrlRejected):
            parse_outbound_url("javascript:alert(1)")
        validator.return_value.validate_url_for_request.assert_not_called()


def test_user_url_fetchers_use_the_gate_and_a_timeout():
    for relative in sorted(GATED_FETCHERS):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert any(token in source for token in GATE_TOKENS), (
            f"{relative} fetches a user-supplied URL without the shared SSRF gate"
        )
        assert "timeout" in source, f"{relative} fetches a remote URL without a timeout"


def test_no_production_module_imports_the_raw_urlopen():
    offenders = []
    for relative, path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "urllib.request":
                continue
            if any(alias.name == "urlopen" for alias in node.names):
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "import urlopen from src.utils.outbound instead of urllib.request: " + ", ".join(offenders)
    )


def test_every_direct_outbound_call_has_a_bounded_timeout():
    all_calls, unbounded = _bounded_http_calls()
    assert len(all_calls) > 10, f"static scan found only {len(all_calls)} HTTP calls"
    assert not unbounded, (
        "outbound calls without a timeout= deadline:\n  "
        + "\n  ".join(f"{path}:{line} {name}" for path, line, name in unbounded)
    )
