"""Regression: the proxy cache-hit SSRF gate must mirror the full validator.

``ExternalProxy._assert_safe_url_structure`` is the only SSRF check that runs
on an in-process cache hit. It must reject the same non-DNS constructs as
``URLValidator.validate_url_for_request`` (backslash authority differentials
and private/reserved IP literals), otherwise a forbidden URL could be served
from the cache without any outbound validation.
"""

from types import SimpleNamespace

import pytest

from src.core.media.exceptions import ProxyFetchError
from src.core.media.security.proxy import ExternalProxy
from src.utils.security import URLValidator


@pytest.fixture
def proxy():
    # _assert_safe_url_structure is network-free; only the URLValidator is
    # needed. No storage/DB/cache work is performed by these tests.
    return ExternalProxy(
        storage_backend=SimpleNamespace(),
        db=SimpleNamespace(),
        allowed_types=ExternalProxy.DEFAULT_ALLOWED_TYPES,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254\\@public.example/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/x",
        "http://[::1]/x",
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_structural_gate_rejects_dangerous_urls(proxy, url):
    with pytest.raises(ProxyFetchError):
        proxy._assert_safe_url_structure(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/image.png",
        "http://8.8.8.8/x.png",
        "https://sub.example.org/a/b?x=1",
    ],
)
def test_structural_gate_allows_public_urls(proxy, url):
    proxy._assert_safe_url_structure(url)


def test_gate_uses_custom_blocked_hosts():
    # A caller-supplied blocked-host set must be honoured on cache hits too.
    validator = URLValidator(blocked_hosts={"corp.internal.example"})
    proxy = ExternalProxy(
        storage_backend=SimpleNamespace(),
        db=SimpleNamespace(),
        allowed_types=ExternalProxy.DEFAULT_ALLOWED_TYPES,
    )
    proxy._url_validator = validator
    with pytest.raises(ProxyFetchError):
        proxy._assert_safe_url_structure("https://corp.internal.example/x.png")
