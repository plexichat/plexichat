"""AutoMod AI backend URLs set through the admin config route must not be SSRF targets.

The ``/admin/automod/config`` write path persists an arbitrary ``ai`` dict. The
custom/OpenAI adapters then POST every moderated message (and the configured
API key) to those URLs. Before the fix the schema accepted any dict, so an
account with only ``admin.moderation.config`` could point a backend at cloud
metadata, loopback or an internal service and use the server as a proxy.

This regression suite pins the write-boundary gate. No app import, DB or real
DNS is needed: private literals are rejected before any lookup, and the one
positive case patches the shared validator's resolution step.
"""

import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError


@contextmanager
def _schema_without_app():
    """Import the schema without building the app, leaving no residue behind.

    ``src/api/__init__`` constructs the whole FastAPI app (and every route) on
    import; the schema only needs the package ``__path__`` for its relative
    imports. The stub is unregistered again as soon as the schema is imported:
    leaving it in ``sys.modules`` replaced the real ``src.api`` for every test
    module imported afterwards in the same worker, so unrelated route tests
    failed with ``ImportError``/``PermissionError`` depending on collection
    order.
    """
    if "src.api" in sys.modules:
        yield
        return
    stub = types.ModuleType("src.api")
    stub.__path__ = [str(Path(__file__).resolve().parents[2] / "api")]
    sys.modules["src.api"] = stub
    try:
        yield
    finally:
        if sys.modules.get("src.api") is stub:
            del sys.modules["src.api"]
        parent = sys.modules.get("src")
        if parent is not None and getattr(parent, "api", None) is stub:
            delattr(parent, "api")


with _schema_without_app():
    from src.api.schemas.admin.automod import AutomodConfigUpdateRequest


pytestmark = [pytest.mark.security, pytest.mark.unit]


def _accept(value):
    return AutomodConfigUpdateRequest(ai=value)


@pytest.mark.parametrize(
    "backend, field",
    [
        ("custom", "endpoint_url"),
        ("openai", "api_url"),
    ],
)
def test_metadata_and_private_addresses_rejected(backend, field):
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/moderate",
        "http://[::1]:8080/moderate",
        "http://localhost/moderate",
    ):
        with pytest.raises(ValidationError):
            _accept({backend: {field: url}})


@pytest.mark.parametrize(
    "backend, field",
    [
        ("custom", "endpoint_url"),
        ("openai", "api_url"),
    ],
)
def test_unsafe_url_shapes_rejected(backend, field):
    for url in (
        "file:///etc/passwd",
        "gopher://internal/redis",
        # A backslash in the authority is parsed as a different host than the
        # one an http client connects to (classic SSRF parser differential).
        "https://169.254.169.254\\@public.example/moderate",
        # Embedded credentials leak the configured API key to a proxy.
        "https://user:pass@public.example/moderate",
    ):
        with pytest.raises(ValidationError):
            _accept({backend: {field: url}})


def test_openai_backend_requires_https(monkeypatch):
    import src.core.security.outbound as outbound

    monkeypatch.setattr(
        outbound.URLValidator,
        "validate_url_for_request",
        lambda self, url: ("moderation.example", "93.184.216.34"),
    )
    with pytest.raises(ValidationError):
        _accept({"openai": {"api_url": "http://moderation.example/v1/moderations"}})


def test_public_urls_accepted_and_preserved(monkeypatch):
    import src.core.security.outbound as outbound

    seen = []

    def _fake_resolve(self, url):
        seen.append(url)
        return ("moderation.example", "93.184.216.34")

    monkeypatch.setattr(
        outbound.URLValidator, "validate_url_for_request", _fake_resolve
    )

    cfg = {
        "custom": {
            "endpoint_url": "https://moderation.example/moderate",
            "threshold": 0.7,
        },
        "openai": {
            "api_url": "https://moderation.example/v1/moderations",
            "model": "x",
        },
    }
    req = _accept(cfg)
    assert req.ai == cfg
    assert len(seen) == 2


def test_shieldstral_local_endpoint_still_allowed():
    """Shieldstral is documented as a local compose service; do not gate it."""
    req = _accept({"shieldstral": {"endpoint_url": "http://shieldstral:8080/v1/chat"}})
    assert req.ai["shieldstral"]["endpoint_url"] == "http://shieldstral:8080/v1/chat"


def test_empty_and_other_fields_untouched():
    assert _accept(None).ai is None
    assert _accept({"enabled": True}).ai == {"enabled": True}
