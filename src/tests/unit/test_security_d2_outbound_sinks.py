"""Every outbound URL sink must be gated (static + runtime evidence).

Attacker-controlled URLs enter through API request models and are handed to an
SSRF guard before anything connects. This module proves two things:

* **statically** -- HTTP-client call sites are enumerated with AST (not string
  grep, which counts ``dict.get`` and comments). Each sink module either calls
  an SSRF guard or is an operator-configured / self-test transport on an
  explicit allowlist. The API layer itself may never call the low-level
  ``URLValidator`` directly; it must delegate to ``src.core.security.outbound``.
* **at runtime** -- constructing real request models with a URL invokes the
  gate, the gate invokes the validator, and a rejected internal address never
  leaks its resolved IP into the client-facing error.
"""

import ast
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.api.schemas.media import HashReportRequest
from src.api.schemas.servers import ServerCreateRequest, ServerUpdateRequest
from src.core.security.errors import OutboundUrlRejected
from src.core.security.outbound import parse_outbound_url

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

HTTP_MODULES = frozenset(
    {"httpx", "requests", "aiohttp", "urllib.request", "src.utils.outbound"}
)
HTTP_METHODS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "request",
        "urlopen",
        "stream",
        "send",
    }
)
GUARD_NAMES = frozenset(
    {
        "URLValidator",
        "validate_url_for_request",
        "resolve_outbound_url",
        "parse_outbound_url",
        "require_https_url",
        "require_http_url",
        "pinned_request_target",
    }
)

# Sinks whose URL is not attacker-controlled: operator config or the self-test
# harness. These are the only HTTP clients allowed to lack an SSRF guard call.
OPERATOR_OR_SELFTEST_SINKS: dict[str, str] = {
    "src/static_client/fetcher.py": "operator-configured GitLab release URL",
    "src/cli/selftest.py": "self-test harness probes a fixed harness URL",
    "src/core/selftest/services/fake_remote.py": "self-test fake remote, not production",
}

# The inbound federation transport resolves *signed* remote endpoints and must
# keep the resolved address pinned across redirects; it implements that with a
# dedicated, stricter path rather than the store-only gate. It is the single
# API-layer exception and must carry all three companion guards.
DIRECT_VALIDATOR_API_SINKS: dict[str, str] = {
    "src/api/routes/federation.py": "signed federation endpoints; pins resolved IP per redirect",
}


def _python_files(base: Path):
    for directory, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if filename.endswith(".py"):
                yield Path(directory) / filename


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _aliases(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in HTTP_MODULES:
                    found.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module in HTTP_MODULES:
            for alias in node.names:
                found.add(alias.asname or alias.name)
    return found


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _called_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


def _http_sinks():
    """Yield ``(relative_path, called_names)`` for modules with HTTP calls."""
    for path in _python_files(SRC):
        tree = _parse(path)
        if tree is None:
            continue
        aliases = _aliases(tree)
        if not aliases:
            continue
        sites = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in HTTP_METHODS
            and _root_name(node.func) in aliases
        ]
        if sites:
            yield str(path.relative_to(ROOT)), _called_names(tree)


def _api_low_level_validator_calls():
    offenders = []
    for path in _python_files(SRC / "api"):
        tree = _parse(path)
        if tree is None:
            continue
        called = _called_names(tree)
        if called & {"URLValidator", "validate_url_for_request"}:
            offenders.append(str(path.relative_to(ROOT)))
    return offenders


class TestStaticSinkAudit:
    def test_sink_discovery_is_not_vacuous(self):
        sinks = dict(_http_sinks())
        assert len(sinks) >= 4, f"only found {len(sinks)} HTTP sink modules"
        # The hardened transcription provider must be among them, so a broken
        # alias scan cannot make the rest of the suite pass vacuously.
        assert "src/core/artifacts/transcription/provider.py" in sinks

    def test_every_http_sink_is_gated_or_explicitly_operator_scoped(self):
        offenders = []
        for relative, called in _http_sinks():
            if called & GUARD_NAMES:
                continue
            if relative in OPERATOR_OR_SELFTEST_SINKS:
                continue
            offenders.append(relative)
        assert not offenders, (
            "Outbound HTTP sinks with no SSRF guard call:\n  "
            + "\n  ".join(sorted(offenders))
        )

    def test_operator_sink_allowlist_has_no_stale_entries(self):
        sinks = set(dict(_http_sinks()))
        stale = sorted(set(OPERATOR_OR_SELFTEST_SINKS) - sinks)
        assert not stale, f"stale outbound sink entries: {stale}"

    def test_api_layer_only_uses_the_validator_in_documented_hardened_sinks(self):
        offenders = [
            path
            for path in _api_low_level_validator_calls()
            if path not in DIRECT_VALIDATOR_API_SINKS
        ]
        assert not offenders, (
            "API modules used URLValidator directly; call "
            "src.core.security.outbound instead: " + ", ".join(offenders)
        )
        # The exception must not lose its companion guards: a bare validator
        # call without pinning/hardening would re-open DNS rebinding.
        for relative in DIRECT_VALIDATOR_API_SINKS:
            tree = _parse(ROOT / relative)
            called = _called_names(tree)  # pyright: ignore[reportArgumentType]
            for companion in (
                "require_https_url",
                "pinned_request_target",
                "is_private_ip",
            ):
                assert companion in called, f"{relative} lost its {companion} guard"

    def test_direct_validator_allowlist_has_no_stale_entries(self):
        current = set(_api_low_level_validator_calls())
        stale = sorted(set(DIRECT_VALIDATOR_API_SINKS) - current)
        assert not stale, f"stale direct-validator entries: {stale}"

    def test_gate_is_the_only_definition_of_the_wrappers(self):
        gate = (ROOT / "src/core/security/outbound.py").read_text(encoding="utf-8")
        assert "def parse_outbound_url" in gate
        assert "def resolve_outbound_url" in gate
        assert "URLValidator().validate_url_for_request" in gate


class TestRuntimeGateInvocation:
    @pytest.mark.parametrize(
        "model,kwargs,field_name,url",
        [
            (
                ServerCreateRequest,
                {"name": "a server"},
                "icon_url",
                "https://example.com/icon.png",
            ),
            (ServerUpdateRequest, {}, "icon_url", "https://example.com/icon.png"),
            (
                HashReportRequest,
                {"hash_value": "a" * 32, "reason": "report"},
                "attachment_url",
                "https://example.com/attachment.png",
            ),
        ],
    )
    def test_request_models_invoke_the_shared_validator(
        self, model, kwargs, field_name, url
    ):
        with patch("src.core.security.outbound.URLValidator") as validator:
            validator.return_value.validate_url_for_request.return_value = (
                "example.com",
                "93.184.216.34",
            )
            model(**kwargs, **{field_name: url})
            validator.return_value.validate_url_for_request.assert_called_once_with(url)

    def test_server_schema_delegates_to_resolve_outbound_url(self):
        with patch("src.api.schemas.servers.resolve_outbound_url") as spy:
            spy.side_effect = lambda value, field: value
            ServerCreateRequest(name="a server", icon_url="https://example.com/i.png")  # pyright: ignore[reportCallIssue]
            spy.assert_called_once_with("https://example.com/i.png", "Server icon URL")

    def test_media_schema_delegates_to_resolve_outbound_url(self):
        with patch("src.api.schemas.media.resolve_outbound_url") as spy:
            spy.side_effect = lambda value, field: value
            HashReportRequest(  # pyright: ignore[reportCallIssue]
                hash_value="a" * 32,
                reason="report",
                attachment_url="https://example.com/a.png",
            )
            spy.assert_called_once_with("https://example.com/a.png", "Attachment URL")

    def test_internal_address_is_rejected_without_echoing_the_ip(self):
        with patch("src.core.security.outbound.URLValidator") as validator:
            validator.return_value.validate_url_for_request.side_effect = ValueError(
                "Access to 10.0.0.5 is forbidden"
            )
            with pytest.raises(ValidationError) as rejection:
                HashReportRequest(  # pyright: ignore[reportCallIssue]
                    hash_value="a" * 32,
                    reason="report",
                    attachment_url="http://internal.example/a.png",
                )
        message = str(rejection.value)
        assert "10.0.0.5" not in message
        assert "Attachment URL" in message

    def test_non_http_schemes_never_reach_the_validator(self):
        with patch("src.core.security.outbound.URLValidator") as validator:
            with pytest.raises(OutboundUrlRejected):
                parse_outbound_url("file:///etc/passwd", "attachment url")
            validator.return_value.validate_url_for_request.assert_not_called()
