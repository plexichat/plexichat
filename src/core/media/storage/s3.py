"""
S3-compatible storage backend.
"""

from typing import BinaryIO, Tuple, Optional

import utils.logger as logger

from .base import StorageBackendBase
from ..exceptions import (
    StorageError,
    StorageConnectionError,
    StorageWriteError,
    StorageReadError,
    StorageDeleteError,
)


class S3Storage(StorageBackendBase):
    """S3-compatible storage backend (AWS S3, MinIO, etc.)."""

    def __init__(
        self,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        endpoint_url: Optional[str] = None,
        use_ssl: bool = True,
        public_url: Optional[str] = None,
        path_prefix: str = "",
    ):
        """
        Initialize S3 storage.

        Args:
            bucket: S3 bucket name
            access_key: AWS access key ID
            secret_key: AWS secret access key
            region: AWS region
            endpoint_url: Custom endpoint URL (for MinIO, etc.)
            use_ssl: Use HTTPS
            public_url: Public URL prefix for serving files
            path_prefix: Prefix for all stored paths
        """
        self._bucket = bucket
        self._region = region
        self._endpoint_url = endpoint_url
        self._use_ssl = use_ssl
        self._public_url = public_url
        self._path_prefix = path_prefix.strip("/")

        try:
            import boto3  # type: ignore[reportMissingImports]
            from botocore.config import Config  # type: ignore[reportMissingImports]
            from botocore.exceptions import ClientError  # type: ignore[reportMissingImports]

            self._ClientError = ClientError
        except ImportError:
            raise StorageError(
                "boto3 is required for S3 storage. Install with: pip install boto3",
                "s3",
            )

        config = Config(
            region_name=region,
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
        )

        client_kwargs = {
            "service_name": "s3",
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "config": config,
            "use_ssl": use_ssl,
        }

        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url

        try:
            self._client = boto3.client(**client_kwargs)
            # Try to validate bucket existence, but don't fail if HeadBucket is unsupported
            try:
                self._client.head_bucket(Bucket=bucket)
            except Exception as head_err:
                logger.warning(f"S3 HeadBucket failed (might be unsupported by provider): {head_err}")
            
            endpoint_info = f" (endpoint: {endpoint_url})" if endpoint_url else ""
            logger.info(f"Connected to S3 storage: bucket={bucket}{endpoint_info}")
        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {e}")
            raise StorageConnectionError(f"Failed to connect to S3: {e}", "s3")

    def _full_path(self, path: str) -> str:
        """Get full S3 key with prefix."""
        clean_path = path.lstrip("/")
        if self._path_prefix:
            return f"{self._path_prefix}/{clean_path}"
        return clean_path

    def store(self, file_data: bytes, path: str, content_type: str) -> str:
        """Store file data at the specified path."""
        key = self._full_path(path)

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=file_data,
                ContentType=content_type,
            )
            logger.debug(
                f"Stored file at s3://{self._bucket}/{key} ({len(file_data)} bytes)"
            )
            return path
        except self._ClientError as e:
            logger.error(f"Failed to store file at s3://{self._bucket}/{key}: {e}")
            raise StorageWriteError(f"Failed to write to S3: {e}", "s3")

    def store_stream(
        self, stream: BinaryIO, path: str, content_type: str, size: int
    ) -> str:
        """Store file from a stream."""
        key = self._full_path(path)

        try:
            self._client.upload_fileobj(
                stream,
                self._bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
            logger.debug(f"Stored stream at s3://{self._bucket}/{key}")
            return path
        except self._ClientError as e:
            logger.error(f"Failed to store stream at s3://{self._bucket}/{key}: {e}")
            raise StorageWriteError(f"Failed to write to S3: {e}", "s3")

    def retrieve(self, path: str) -> bytes:
        """Retrieve file data from storage."""
        key = self._full_path(path)

        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            data = response["Body"].read()
            logger.debug(
                f"Retrieved file from s3://{self._bucket}/{key} ({len(data)} bytes)"
            )
            return data
        except self._ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                raise StorageReadError(f"File not found: {path}", "s3")
            logger.error(f"Failed to read file from s3://{self._bucket}/{key}: {e}")
            raise StorageReadError(f"Failed to read from S3: {e}", "s3")

    def retrieve_stream(self, path: str) -> Tuple[BinaryIO, int]:
        """Retrieve file as a stream."""
        key = self._full_path(path)

        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            size = response["ContentLength"]
            return response["Body"], size
        except self._ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                raise StorageReadError(f"File not found: {path}", "s3")
            logger.error(f"Failed to open stream from s3://{self._bucket}/{key}: {e}")
            raise StorageReadError(f"Failed to read from S3: {e}", "s3")

    def delete(self, path: str) -> bool:
        """Delete file from storage."""
        key = self._full_path(path)

        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            logger.debug(f"Deleted file at s3://{self._bucket}/{key}")
            return True
        except self._ClientError as e:
            logger.error(f"Failed to delete file at s3://{self._bucket}/{key}: {e}")
            raise StorageDeleteError(f"Failed to delete from S3: {e}", "s3")

    def exists(self, path: str) -> bool:
        """Check if file exists in storage."""
        key = self._full_path(path)

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._ClientError:
            return False

    def get_url(self, path: str) -> str:
        """Get public URL for file."""
        key = self._full_path(path)

        if self._public_url:
            return f"{self._public_url.rstrip('/')}/{key}"

        if self._endpoint_url:
            return f"{self._endpoint_url.rstrip('/')}/{self._bucket}/{key}"

        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"

    def get_size(self, path: str) -> int:
        """Get file size."""
        key = self._full_path(path)

        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
            return response["ContentLength"]
        except self._ClientError:
            return 0

    def is_encrypted(self, path: str) -> bool:
        """Check if file is encrypted."""
        return False

    def get_metadata(self, path: str) -> dict:
        """Get file metadata."""
        key = self._full_path(path)

        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
            return {
                "path": path,
                "key": key,
                "bucket": self._bucket,
                "exists": True,
                "size": response["ContentLength"],
                "content_type": response.get("ContentType"),
                "last_modified": response.get("LastModified"),
                "etag": response.get("ETag", "").strip('"'),
            }
        except self._ClientError:
            return {
                "path": path,
                "key": key,
                "bucket": self._bucket,
                "exists": False,
            }

    def generate_presigned_url(
        self, path: str, expires_in: int = 3600, params: Optional[dict] = None
    ) -> str:
        """
        Generate a presigned URL for temporary access.

        Args:
            path: Storage path
            expires_in: URL expiration time in seconds
            params: Optional extra parameters for the GET request (e.g. ResponseContentDisposition)

        Returns:
            Presigned URL
        """
        key = self._full_path(path)
        get_params = {"Bucket": self._bucket, "Key": key}
        if params:
            get_params.update(params)

        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params=get_params,
                ExpiresIn=expires_in,
            )
            return url
        except self._ClientError as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            raise StorageError(f"Failed to generate presigned URL: {e}", "s3")

    def generate_presigned_upload(
        self,
        path: str,
        content_type: str,
        expires_in: int = 3600,
        max_size: Optional[int] = None,
    ) -> dict:
        """
        Generate presigned POST data for direct upload.

        Args:
            path: Storage path
            content_type: Expected content type
            expires_in: URL expiration time in seconds
            max_size: Maximum file size in bytes

        Returns:
            Dict with url and fields for POST request
        """
        key = self._full_path(path)

        conditions: list[dict[str, str] | list[str | int]] = [
            {"Content-Type": content_type},
        ]

        if max_size:
            conditions.append(["content-length-range", 0, max_size])

        try:
            response = self._client.generate_presigned_post(
                Bucket=self._bucket,
                Key=key,
                Fields={"Content-Type": content_type},
                Conditions=conditions,
                ExpiresIn=expires_in,
            )
            return response
        except self._ClientError as e:
            logger.error(f"Failed to generate presigned POST: {e}")
            raise StorageError(f"Failed to generate presigned POST: {e}", "s3")
