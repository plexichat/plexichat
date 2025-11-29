"""
Media module test fixtures.
"""

import os
import sys
import io
import shutil
import tempfile
import pytest

# Ensure paths are set up before any imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, utils_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture(scope="session")
def media_test_dir():
    """Create a temporary directory for media tests."""
    test_dir = tempfile.mkdtemp(prefix="media_test_")
    yield test_dir
    shutil.rmtree(test_dir, ignore_errors=True)


@pytest.fixture
def temp_upload_dir(media_test_dir):
    """Create a fresh upload directory for each test."""
    upload_dir = os.path.join(media_test_dir, f"uploads_{os.getpid()}")
    os.makedirs(upload_dir, exist_ok=True)
    yield upload_dir
    shutil.rmtree(upload_dir, ignore_errors=True)


@pytest.fixture
def sample_image_bytes():
    """Generate a minimal valid JPEG image."""
    try:
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="red")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        return buffer.getvalue()
    except ImportError:
        return (
            b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
            b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t'
            b'\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a'
            b'\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9teletext'
            b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
            b'\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00'
            b'\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b'
            b'\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04'
            b'\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa'
            b'\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n'
            b'\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz'
            b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5\x00\x00\x00\x00'
            b'\xff\xd9'
        )


@pytest.fixture
def sample_png_bytes():
    """Generate a minimal valid PNG image."""
    try:
        from PIL import Image
        img = Image.new("RGBA", (50, 50), color=(0, 255, 0, 255))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
    except ImportError:
        return (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00'
            b'\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc'
            b'\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND'
            b'\xaeB`\x82'
        )


@pytest.fixture
def sample_gif_bytes():
    """Generate a minimal valid GIF image."""
    try:
        from PIL import Image
        img = Image.new("P", (10, 10))
        buffer = io.BytesIO()
        img.save(buffer, format="GIF")
        return buffer.getvalue()
    except ImportError:
        return (
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!'
            b'\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00'
            b'\x00\x02\x02D\x01\x00;'
        )


@pytest.fixture
def sample_text_bytes():
    """Generate sample text file content."""
    return b"Hello, this is a test file content."


@pytest.fixture
def sample_pdf_bytes():
    """Generate minimal PDF content."""
    return (
        b'%PDF-1.4\n'
        b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
        b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
        b'3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n'
        b'xref\n0 4\n0000000000 65535 f \n'
        b'0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n'
        b'trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF'
    )


@pytest.fixture
def large_file_bytes():
    """Generate a file larger than typical limits for testing."""
    return b"x" * (11 * 1024 * 1024)


@pytest.fixture
def media_module(modules, temp_upload_dir):
    """Get configured media module for testing."""
    import utils.config as config
    
    original_media_config = config.get("media", {})
    
    test_config = {
        "storage_backend": "local",
        "local_path": temp_upload_dir,
        "local_url": "/media",
        "size_limits": {
            "image": 10 * 1024 * 1024,
            "video": 100 * 1024 * 1024,
            "audio": 50 * 1024 * 1024,
            "document": 25 * 1024 * 1024,
            "other": 10 * 1024 * 1024,
        },
        "thumbnail_sizes": [64, 128, 256, 512],
        "signing_key": "test-secret-key-for-signing",
        "signing_expiry": 3600,
        "scanner_enabled": False,
        "proxy_enabled": False,
    }
    
    config._config_instance.config["media"] = test_config
    
    from src.core import media
    media._manager = None
    media._setup_complete = False
    media.setup(modules._db, modules.messaging)
    
    yield media
    
    config._config_instance.config["media"] = original_media_config
    media._manager = None
    media._setup_complete = False


@pytest.fixture
def mock_s3_client(mocker):
    """Mock boto3 S3 client for S3 storage tests."""
    mock_client = mocker.MagicMock()
    mock_client.head_bucket.return_value = {}
    mock_client.put_object.return_value = {}
    mock_client.get_object.return_value = {
        "Body": io.BytesIO(b"test content"),
        "ContentLength": 12,
        "ContentType": "application/octet-stream",
    }
    mock_client.head_object.return_value = {
        "ContentLength": 12,
        "ContentType": "application/octet-stream",
    }
    mock_client.delete_object.return_value = {}
    
    mock_boto3 = mocker.patch("boto3.client", return_value=mock_client)
    
    return mock_client


def pytest_configure(config):
    """Register media test markers and ensure paths are set."""
    import sys
    import os
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    for p in [project_root, os.path.join(project_root, "src"), 
              os.path.join(project_root, "src", "utils"),
              os.path.join(project_root, "src", "utils", "common-utils")]:
        if p not in sys.path:
            sys.path.insert(0, p)
    
    config.addinivalue_line("markers", "media: Media module tests")
    config.addinivalue_line("markers", "pillow: Tests requiring Pillow")
    config.addinivalue_line("markers", "ffprobe: Tests requiring ffprobe")
    config.addinivalue_line("markers", "s3: Tests for S3 storage backend")
