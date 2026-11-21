"""DNS-rebinding regression for the search-backend outbound transport."""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]


def _resp(status=200, body=b"{}"):
    r = MagicMock()
    r.status = status
    r.read.return_value = body
    return r


def test_pinned_urlopen_connects_to_resolved_ip_with_real_host_header():
    from src.utils import outbound

    conn = MagicMock()
    conn.getresponse.return_value = _resp(200, b'{"ok": true}')
    with (
        patch("http.client.HTTPConnection", return_value=conn) as ctor,
        patch(
            "src.utils.security.URLValidator.validate_url_for_request",
            return_value=("search.example", "93.184.216.34"),
        ),
    ):
        with outbound.pinned_urlopen(
            "http://search.example:8443/path?q=1",
            method="POST",
            data=b"body",
            headers={"Authorization": "Bearer x"},
            timeout=7,
        ) as result:
            assert result.read() == b'{"ok": true}'

    ctor.assert_called_once_with("93.184.216.34", 8443, timeout=7)
    args, kwargs = conn.request.call_args
    assert args[0] == "POST"
    assert args[1] == "/path?q=1"
    assert kwargs["headers"]["Host"] == "search.example:8443"
    assert kwargs["headers"]["Authorization"] == "Bearer x"


def test_pinned_urlopen_uses_hostname_for_tls_sni():
    from src.utils import outbound

    conn = MagicMock()
    conn.getresponse.return_value = _resp()
    with (
        patch.object(outbound, "_PinnedHTTPSConnection", return_value=conn) as ctor,
        patch(
            "src.utils.security.URLValidator.validate_url_for_request",
            return_value=("search.example", "93.184.216.34"),
        ),
    ):
        with outbound.pinned_urlopen("https://search.example/x"):
            pass
    assert ctor.call_args.args[0] == "93.184.216.34"
    assert ctor.call_args.kwargs["sni_hostname"] == "search.example"


def test_pinned_https_connection_sni_not_pinned_ip():
    from src.utils import outbound

    c = outbound._PinnedHTTPSConnection.__new__(outbound._PinnedHTTPSConnection)
    c._tunnel_host = None
    c._sni_hostname = "search.example"
    c.sock = MagicMock()
    c._context = MagicMock()
    with patch("http.client.HTTPConnection.connect") as base:
        c.connect()
    base.assert_called_once_with(c)
    assert (
        c._context.wrap_socket.call_args.kwargs["server_hostname"] == "search.example"
    )


def test_pinned_urlopen_rejects_forbidden_target_before_connect():
    from src.utils import outbound

    with (
        patch(
            "src.utils.security.URLValidator.validate_url_for_request",
            side_effect=ValueError("forbidden"),
        ),
        patch("http.client.HTTPConnection") as ctor,
    ):
        with pytest.raises(ValueError):
            outbound.pinned_urlopen("http://169.254.169.254/latest/meta-data/")
    ctor.assert_not_called()


def test_search_indexers_route_requests_through_pinned_transport():
    import inspect
    from src.core.search.indexer import meilisearch, elasticsearch

    for mod in (meilisearch, elasticsearch):
        src = inspect.getsource(mod)
        assert "pinned_urlopen" in src, mod.__name__
        assert "with urlopen(" not in src, mod.__name__
