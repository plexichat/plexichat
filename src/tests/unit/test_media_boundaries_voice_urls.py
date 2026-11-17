"""Voice STUN/TURN URLs are scheme-checked and length-bounded."""

import pytest

from src.core.voice.boundaries import (
    DEFAULT_PUBLIC_STUN_URLS,
    MAX_ENDPOINT_URL_LENGTH,
    MAX_ICE_SERVERS,
    MAX_ICE_URL_LENGTH,
    filter_ice_urls,
    sanitize_ice_servers,
    validate_ice_url,
    validate_server_endpoint,
)


class TestIceUrls:
    @pytest.mark.parametrize(
        "value",
        [
            "stun:stun.l.google.com:19302",
            "stuns:stun.example.com:5349",
            "turn:turn.example.com:3478",
            "turns:turn.example.com:5349",
            "turn:[2001:db8::1]:3478",
        ],
    )
    def test_valid_ice_urls(self, value):
        assert validate_ice_url(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "javascript:alert(1)",
            "https://example.com/x",
            "stun:",
            "stun:user:pass@example.com:3478",
            "stun:host<script>",
            "stun:" + "a" * (MAX_ICE_URL_LENGTH + 1),
            "stun:host\x00bad",
        ],
    )
    def test_invalid_ice_urls_rejected(self, value):
        with pytest.raises(ValueError):
            validate_ice_url(value)

    def test_filter_keeps_valid_and_drops_invalid(self):
        result = filter_ice_urls(["stun:good.example.com:19302", "javascript:alert(1)"])
        assert "stun:good.example.com:19302" in result
        assert all("javascript" not in u for u in result)

    def test_filter_falls_back_to_public_stun(self):
        assert filter_ice_urls(["javascript:alert(1)"]) == DEFAULT_PUBLIC_STUN_URLS


class TestServerEndpoint:
    def test_websocket_schemes_accepted(self):
        assert validate_server_endpoint("wss://voice.example.com:4443/ws")
        assert validate_server_endpoint("ws://localhost:4443/")

    def test_unsafe_schemes_rejected(self):
        for value in ("javascript:alert(1)", "https://x", "//x", "wss://user:pw@x"):
            with pytest.raises(ValueError):
                validate_server_endpoint(value)

    def test_oversize_rejected(self):
        with pytest.raises(ValueError):
            validate_server_endpoint(
                "https://example.com/" + "a" * MAX_ENDPOINT_URL_LENGTH
            )


class TestIceServerSanitization:
    class _Server:
        def __init__(self, urls, username=None, credential=None):
            self.urls = urls
            self.username = username
            self.credential = credential

    def test_caps_server_count(self):
        servers = [
            self._Server(["stun:s.example.com:19302"])
            for _ in range(MAX_ICE_SERVERS + 5)
        ]
        assert len(sanitize_ice_servers(servers)) == MAX_ICE_SERVERS

    def test_drops_servers_with_only_invalid_urls(self):
        servers = [self._Server(["javascript:alert(1)"])]
        assert sanitize_ice_servers(servers) == []

    def test_valid_server_kept(self):
        servers = [self._Server(["turn:turn.example.com:3478"], "u", "c")]
        result = sanitize_ice_servers(servers)
        assert result[0]["urls"] == ["turn:turn.example.com:3478"]
        assert result[0]["username"] == "u"
