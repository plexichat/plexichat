"""
Media exceptions - All media-related error types.
"""


class MediaError(Exception):
    """Base exception for all media errors."""
    pass


class FileNotFoundError(MediaError):
    """File does not exist."""
    pass


class FileUploadError(MediaError):
    """File upload failed."""
    
    def __init__(self, message: str, filename: str = None):
        super().__init__(message)
        self.filename = filename


class FileSizeError(MediaError):
    """File exceeds size limit."""
    
    def __init__(self, message: str, max_size: int, actual_size: int):
        super().__init__(message)
        self.max_size = max_size
        self.actual_size = actual_size


class FileTypeError(MediaError):
    """File type not allowed."""
    
    def __init__(self, message: str, content_type: str = None, allowed_types: list = None):
        super().__init__(message)
        self.content_type = content_type
        self.allowed_types = allowed_types or []


class StorageError(MediaError):
    """Storage backend error."""
    
    def __init__(self, message: str, backend: str = None):
        super().__init__(message)
        self.backend = backend


class StorageConnectionError(StorageError):
    """Failed to connect to storage backend."""
    pass


class StorageWriteError(StorageError):
    """Failed to write to storage."""
    pass


class StorageReadError(StorageError):
    """Failed to read from storage."""
    pass


class StorageDeleteError(StorageError):
    """Failed to delete from storage."""
    pass


class ImageProcessingError(MediaError):
    """Image processing failed."""
    
    def __init__(self, message: str, operation: str = None):
        super().__init__(message)
        self.operation = operation


class VideoProcessingError(MediaError):
    """Video processing failed."""
    
    def __init__(self, message: str, operation: str = None):
        super().__init__(message)
        self.operation = operation


class SigningError(MediaError):
    """URL signing failed."""
    pass


class SignatureExpiredError(MediaError):
    """Signed URL has expired."""
    pass


class SignatureInvalidError(MediaError):
    """Signature verification failed."""
    pass


class ProxyError(MediaError):
    """External URL proxy error."""
    
    def __init__(self, message: str, url: str = None):
        super().__init__(message)
        self.url = url


class ProxyFetchError(ProxyError):
    """Failed to fetch external URL."""
    pass


class ProxyCacheError(ProxyError):
    """Failed to cache proxied content."""
    pass


class ScannerError(MediaError):
    """Malware scanner error."""
    pass


class MalwareDetectedError(MediaError):
    """Malware detected in file."""
    
    def __init__(self, message: str, threat_name: str = None):
        super().__init__(message)
        self.threat_name = threat_name


class ScannerUnavailableError(ScannerError):
    """Scanner service unavailable."""
    pass


class PermissionDeniedError(MediaError):
    """User does not have permission to perform this action."""
    pass
