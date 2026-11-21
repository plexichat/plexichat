"""Regression: operator admin host allowlists must not be silently widened.

The config deep-merge concatenates the default ``allowed_hosts``
(``127.0.0.1``, ``localhost``, ``::1``) into whatever an operator configured.
An explicit narrow allowlist therefore kept permitting loopback peers, and an
explicit empty allowlist never reached the core gate's fail-closed path. Behind
a local reverse proxy with no ``api.trusted_proxies`` configured, every remote
peer appears as loopback, so this turned the admin host restriction into an
allow-all rule. The admin gate now honours the explicit operator allowlist.
"""

import pytest
import yaml
from fastapi import HTTPException
from starlette.requests import Request

import src.api.routes.admin.utils as admin_utils


def _setup(tmp_path, monkeypatch, operator_hosts, explicit=True):
    """Point the gate at a raw config file carrying operator host values."""
    doc = {
        "api": {"trusted_proxies": []},
        "admin_ui": {
            "enabled": True,
            "host_restriction": {"enabled": True},
        },
    }
    if explicit:
        doc["admin_ui"]["host_restriction"]["allowed_hosts"] = operator_hosts
    path = tmp_path / "admin.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    # The merged config still carries the widened defaults; patching config.get
    # to that value proves the gate relies on the raw file, not the merged one.
    merged = {
        "api": {"trusted_proxies": []},
        "admin_ui": {
            "enabled": True,
            "host_restriction": {
                "enabled": True,
                "allowed_hosts": ["127.0.0.1", "localhost", "::1"],
            },
        },
    }

    import utils.config as config

    class _Instance:
        config_path = str(path)

    monkeypatch.setattr(config, "_config_instance", _Instance(), raising=False)
    monkeypatch.setattr(
        config, "get", lambda key, default=None: merged.get(key, default)
    )
    from src.utils import licensing

    monkeypatch.setattr(licensing, "is_provisional", lambda: False)
    admin_utils._explicit_hosts_cache["key"] = None
    admin_utils._explicit_hosts_cache["value"] = None


def _request(client_host):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/dashboard",
            "headers": [(b"host", b"testserver")],
            "client": (client_host, 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


def test_narrow_allowlist_denies_loopback_peer(tmp_path, monkeypatch):
    """A loopback peer must be denied when the operator removed loopback.

    Before the fix the merged config re-added 127.0.0.1 and this request
    passed the admin host gate.
    """
    _setup(tmp_path, monkeypatch, ["198.51.100.0/24"])
    with pytest.raises(HTTPException) as exc_info:
        admin_utils.check_host_restriction(_request("127.0.0.1"))
    assert exc_info.value.status_code == 403


def test_empty_allowlist_denies_loopback_peer(tmp_path, monkeypatch):
    """An explicitly empty allowlist reaches the core gate's fail-closed path."""
    _setup(tmp_path, monkeypatch, [])
    with pytest.raises(HTTPException) as exc_info:
        admin_utils.check_host_restriction(_request("127.0.0.1"))
    assert exc_info.value.status_code == 403


def test_narrow_allowlist_still_admits_configured_peer(tmp_path, monkeypatch):
    """The narrowed allowlist is enforced, not replaced with deny-all."""
    _setup(tmp_path, monkeypatch, ["198.51.100.0/24"])
    admin_utils.check_host_restriction(_request("198.51.100.5"))


def test_absent_allowlist_uses_merged_defaults(tmp_path, monkeypatch):
    """With no explicit allowlist the merged default still allows loopback."""
    _setup(tmp_path, monkeypatch, None, explicit=False)
    admin_utils.check_host_restriction(_request("127.0.0.1"))
