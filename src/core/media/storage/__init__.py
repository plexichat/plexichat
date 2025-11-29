"""
Storage backends for media module.
"""

from .base import StorageBackendBase
from .local import LocalStorage
from .s3 import S3Storage

__all__ = ['StorageBackendBase', 'LocalStorage', 'S3Storage']
