"""Regression: the explicit admin host allowlist lookup must not ``stat`` config.yaml
on every admin request.

``check_host_restriction`` runs on every admin route, and each call funnelled
through ``_explicit_admin_allowed_hosts`` did an unconditional ``os.stat`` of the
config file even though the parsed value was already cached by (path, mtime,
size).  That makes the stat a fixed per-request cost on a hot path.  A short
staleness ceiling now throttles the stat itself; the parsed value is still
version-keyed, so a real config edit is observed once the ceiling elapses (or
immediately via ``invalidate_explicit_hosts_cache``).
"""

import time

import yaml

import utils.config as config
import src.api.routes.admin.utils as admin_utils


def _setup(tmp_path, monkeypatch, allowed_hosts):
    """Point the gate at a raw config file carrying an explicit allowlist."""
    doc = {
        "api": {"trusted_proxies": []},
        "admin_ui": {
            "enabled": True,
            "host_restriction": {
                "enabled": True,
                "allowed_hosts": allowed_hosts,
            },
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    class _Instance:
        config_path = str(path)

    monkeypatch.setattr(config, "_config_instance", _Instance(), raising=False)
    admin_utils.invalidate_explicit_hosts_cache()
    return path


def _count_config_stats(monkeypatch, path):
    """Count os.stat calls issued against the config file under test."""
    real_stat = admin_utils.os.stat
    calls = [0]

    def counting_stat(p, *args, **kwargs):
        if str(p) == str(path):
            calls[0] += 1
        return real_stat(p, *args, **kwargs)

    monkeypatch.setattr(admin_utils.os, "stat", counting_stat)
    return calls


def test_stat_throttled_on_hot_path(tmp_path, monkeypatch):
    """Twenty-five consecutive reads issue a single stat, not twenty-five."""
    path = _setup(tmp_path, monkeypatch, ["198.51.100.0/24"])
    calls = _count_config_stats(monkeypatch, path)

    for _ in range(25):
        assert admin_utils._explicit_admin_allowed_hosts() == ["198.51.100.0/24"]

    # Before the fix this was one stat per call (25). The ceiling bounds it to
    # a single stat for the whole window.
    assert calls[0] == 1, f"expected 1 config stat in a 5s window, got {calls[0]}"


def test_config_change_observed_after_ceiling(tmp_path, monkeypatch):
    """A real on-disk edit is still honoured once the ceiling elapses."""
    path = _setup(tmp_path, monkeypatch, ["198.51.100.0/24"])
    assert admin_utils._explicit_admin_allowed_hosts() == ["198.51.100.0/24"]

    path.write_text(
        yaml.safe_dump(
            {
                "api": {"trusted_proxies": []},
                "admin_ui": {
                    "enabled": True,
                    "host_restriction": {
                        "enabled": True,
                        "allowed_hosts": ["203.0.113.0/24", "192.0.2.0/24"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    # Still within the ceiling: the previously parsed value is served.
    assert admin_utils._explicit_admin_allowed_hosts() == ["198.51.100.0/24"]

    # Force the ceiling to elapse; the version-keyed cache then re-parses.
    admin_utils._explicit_hosts_cache["checked_at"] = time.monotonic() - 60
    assert admin_utils._explicit_admin_allowed_hosts() == [
        "203.0.113.0/24",
        "192.0.2.0/24",
    ]


def test_explicit_invalidate_forces_immediate_reread(tmp_path, monkeypatch):
    """Admin config-reload callers can drop the cached verdict immediately."""
    path = _setup(tmp_path, monkeypatch, ["198.51.100.0/24"])
    assert admin_utils._explicit_admin_allowed_hosts() == ["198.51.100.0/24"]

    path.write_text(
        yaml.safe_dump(
            {
                "api": {"trusted_proxies": []},
                "admin_ui": {
                    "enabled": True,
                    "host_restriction": {
                        "enabled": True,
                        "allowed_hosts": ["203.0.113.0/24"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    admin_utils.invalidate_explicit_hosts_cache()
    assert admin_utils._explicit_admin_allowed_hosts() == ["203.0.113.0/24"]
