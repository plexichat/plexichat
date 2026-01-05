"""
Storage backends for media module.
"""

from .base import StorageBackendBase
from .local import LocalStorage
from .s3 import S3Storage
from .database import DatabaseStorage
from .encrypted import EncryptedStorage, wrap_storage_with_encryption

__all__ = [
    'StorageBackendBase', 
    'LocalStorage', 
    'S3Storage', 
    'DatabaseStorage',
    'EncryptedStorage',
    'wrap_storage_with_encryption',
]
