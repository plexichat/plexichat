"""
Tests for external URL proxy functionality.
"""

import pytest


@pytest.mark.media
class TestExternalProxy:
    """Tests for ExternalProxy class."""

    def test_validate_url_rejects_invalid_scheme(self, temp_upload_dir, modules):
        """Test that invalid URL schemes are rejected."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage
        from src.core.media.exceptions import ProxyFetchError

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        with pytest.raises(ProxyFetchError):
            proxy._validate_url("ftp://example.com/file.jpg")

    def test_validate_url_rejects_no_host(self, temp_upload_dir, modules):
        """Test that URLs without host are rejected."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage
        from src.core.media.exceptions import ProxyFetchError

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        with pytest.raises(ProxyFetchError):
            proxy._validate_url("http:///path/file.jpg")

    def test_validate_url_accepts_http(self, temp_upload_dir, modules):
        """Test that HTTP URLs are accepted."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        proxy._validate_url("http://example.com/file.jpg")

    def test_validate_url_accepts_https(self, temp_upload_dir, modules):
        """Test that HTTPS URLs are accepted."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        proxy._validate_url("https://example.com/file.jpg")

    def test_get_extension_for_content_type(self, temp_upload_dir, modules):
        """Test getting file extension for content type."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        assert proxy._get_extension("image/jpeg") == ".jpg"
        assert proxy._get_extension("image/png") == ".png"
        assert proxy._get_extension("image/gif") == ".gif"
        assert proxy._get_extension("image/webp") == ".webp"
        assert proxy._get_extension("application/octet-stream") == ".bin"

    def test_is_cached_returns_false_for_uncached(self, temp_upload_dir, modules):
        """Test that is_cached returns False for uncached URLs."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        assert proxy.is_cached("https://example.com/uncached.jpg") is False

    def test_invalidate_uncached_returns_false(self, temp_upload_dir, modules):
        """Test that invalidating uncached URL returns False."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        result = proxy.invalidate("https://example.com/not_cached.jpg")
        assert result is False


@pytest.mark.media
class TestProxyWithMockedRequests:
    """Tests for proxy with mocked HTTP requests."""

    def test_fetch_caches_content(self, temp_upload_dir, modules, mocker):
        """Test that fetch caches content."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_response.iter_content.return_value = [b"fake image data"]
        mock_response.raise_for_status.return_value = None

        mock_get = mocker.patch("requests.get", return_value=mock_response)

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        result = proxy.fetch("https://example.com/image.jpg")

        assert result.source_url == "https://example.com/image.jpg"
        assert result.content_type == "image/jpeg"
        assert proxy.is_cached("https://example.com/image.jpg") is True

    def test_fetch_returns_cached_on_second_call(self, temp_upload_dir, modules, mocker):
        """Test that second fetch returns cached content."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.iter_content.return_value = [b"png data"]
        mock_response.raise_for_status.return_value = None

        mock_get = mocker.patch("requests.get", return_value=mock_response)

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        proxy.fetch("https://example.com/cached.png")
        proxy.fetch("https://example.com/cached.png")

        assert mock_get.call_count == 1

    def test_fetch_force_refresh_bypasses_cache(self, temp_upload_dir, modules, mocker):
        """Test that force_refresh bypasses cache."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "image/gif"}
        mock_response.iter_content.return_value = [b"gif data"]
        mock_response.raise_for_status.return_value = None

        mock_get = mocker.patch("requests.get", return_value=mock_response)

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        proxy.fetch("https://example.com/refresh.gif")
        proxy.fetch("https://example.com/refresh.gif", force_refresh=True)

        assert mock_get.call_count == 2

    def test_fetch_rejects_disallowed_content_type(self, temp_upload_dir, modules, mocker):
        """Test that disallowed content types are rejected."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage
        from src.core.media.exceptions import ProxyFetchError

        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "application/javascript"}
        mock_response.raise_for_status.return_value = None

        mocker.patch("requests.get", return_value=mock_response)

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        with pytest.raises(ProxyFetchError):
            proxy.fetch("https://example.com/script.js")

    def test_fetch_rejects_oversized_content(self, temp_upload_dir, modules, mocker):
        """Test that oversized content is rejected."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage
        from src.core.media.exceptions import ProxyFetchError

        mock_response = mocker.MagicMock()
        mock_response.headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": "999999999",
        }
        mock_response.raise_for_status.return_value = None

        mocker.patch("requests.get", return_value=mock_response)

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(
            storage_backend=storage,
            db=modules._db,
            max_size=1024,
        )

        with pytest.raises(ProxyFetchError):
            proxy.fetch("https://example.com/huge.jpg")

    def test_get_content_returns_data_and_type(self, temp_upload_dir, modules, mocker):
        """Test that get_content returns data and content type."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "image/webp"}
        mock_response.iter_content.return_value = [b"webp content"]
        mock_response.raise_for_status.return_value = None

        mocker.patch("requests.get", return_value=mock_response)

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        data, content_type = proxy.get_content("https://example.com/image.webp")

        assert data == b"webp content"
        assert content_type == "image/webp"

    def test_invalidate_removes_cached_content(self, temp_upload_dir, modules, mocker):
        """Test that invalidate removes cached content."""
        from src.core.media.security.proxy import ExternalProxy
        from src.core.media.storage.local import LocalStorage

        mock_response = mocker.MagicMock()
        mock_response.headers = {"Content-Type": "image/jpeg"}
        mock_response.iter_content.return_value = [b"data"]
        mock_response.raise_for_status.return_value = None

        mocker.patch("requests.get", return_value=mock_response)

        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        proxy = ExternalProxy(storage_backend=storage, db=modules._db)

        proxy.fetch("https://example.com/invalidate.jpg")
        assert proxy.is_cached("https://example.com/invalidate.jpg") is True

        result = proxy.invalidate("https://example.com/invalidate.jpg")

        assert result is True
        assert proxy.is_cached("https://example.com/invalidate.jpg") is False
