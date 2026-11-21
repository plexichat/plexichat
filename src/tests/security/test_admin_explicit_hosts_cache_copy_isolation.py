"""Regression: the cached explicit admin host allowlist must be copy-on-read.

``_explicit_hosts_cache`` is a process-wide memo keyed only by config-file
metadata. Its ``value`` is a mutable list of allowed hosts that feeds
``check_host_restriction`` on every admin request. Handing that list out by
reference would let any caller's in-place edit (e.g. appending a permissive
CIDR) silently widen the admin host gate for every later request from every
user for the rest of the staleness window -- the same cross-request cache
poisoning class as the capability verdict cache.

This pins the copy contract on all three cache paths (cold build, version-key
hit, staleness-ceiling hit) and proves the host gate stays closed.
"""

import time

import pytest
import yaml

import utils.config as config
import src.api.routes.admin.utils as admin_utils
from src.core import admin as core_admin

pytestmark = pytest.mark.unit

ALLOWED = ["198.51.100.0/24"]


@pytest.fixture
def _allowlist(tmp_path, monkeypatch):
    doc = {
        "api": {"trusted_proxies": []},
        "admin_ui": {
            "enabled": True,
            "host_restriction": {"enabled": True, "allowed_hosts": list(ALLOWED)},
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    class _Instance:
        config_path = str(path)

    monkeypatch.setattr(config, "_config_instance", _Instance(), raising=False)
    admin_utils.invalidate_explicit_hosts_cache()
    yield
    admin_utils.invalidate_explicit_hosts_cache()


def _poison(returned):
    """Mutate a returned allowlist the way a buggy caller would."""
    returned.append("0.0.0.0/0")  # would widen the admin gate to every host
    returned[0] = "127.0.0.1"


def test_cold_miss_result_is_detached_from_the_memo(_allowlist):
    first = admin_utils._explicit_admin_allowed_hosts()
    assert first == ALLOWED
    original = list(first)

    _poison(first)
    assert first != original  # sanity: the caller really mutated its copy

    # A later request served from the memo must see the pristine allowlist.
    second = admin_utils._explicit_admin_allowed_hosts()
    assert second is not first
    assert second == original
    assert admin_utils._explicit_hosts_cache["value"] == original

    # Security impact: the injected 0.0.0.0/0 must not open the admin gate.
    assert core_admin.check_host_restriction("203.0.113.9", second) is False
    assert core_admin.check_host_restriction("203.0.113.9", first) is True


def test_version_key_hit_is_detached_from_the_memo(_allowlist):
    first = admin_utils._explicit_admin_allowed_hosts()
    assert first == ALLOWED

    # Elapse the ceiling so the version check runs; the on-disk key is
    # unchanged, taking the "key match" cache-hit path.
    admin_utils._explicit_hosts_cache["checked_at"] = time.monotonic() - 60
    key_hit = admin_utils._explicit_admin_allowed_hosts()
    assert key_hit == ALLOWED

    _poison(key_hit)
    assert admin_utils._explicit_hosts_cache["value"] == ALLOWED
    assert admin_utils._explicit_admin_allowed_hosts() == ALLOWED


def test_staleness_ceiling_hit_is_detached_from_the_memo(_allowlist):
    first = admin_utils._explicit_admin_allowed_hosts()
    assert first == ALLOWED

    ceiling_hit = admin_utils._explicit_admin_allowed_hosts()
    assert ceiling_hit == ALLOWED

    _poison(ceiling_hit)
    assert admin_utils._explicit_hosts_cache["value"] == ALLOWED
    assert admin_utils._explicit_admin_allowed_hosts() == ALLOWED
