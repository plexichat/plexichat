"""
Malware scanning interface for ClamAV.
"""

import socket
import struct
from typing import Optional, Tuple

import utils.logger as logger

from ..models import ScanStatus
from ..exceptions import ScannerError, ScannerUnavailableError


class MalwareScanner:
    """Malware scanner interface for ClamAV."""

    CHUNK_SIZE = 8192

    def __init__(
        self,
        host: str = "localhost",
        port: int = 3310,
        timeout: int = 30,
        enabled: bool = True,
    ):
        """
        Initialize malware scanner.

        Args:
            host: ClamAV daemon host
            port: ClamAV daemon port
            timeout: Socket timeout in seconds
            enabled: Whether scanning is enabled
        """
        self._host = host
        self._port = port
        self._timeout = timeout
        self._enabled = enabled

    def is_available(self) -> bool:
        """Check if ClamAV daemon is available."""
        if not self._enabled:
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((self._host, self._port))
            sock.send(b"PING\n")
            response = sock.recv(1024)
            sock.close()
            return response.strip() == b"PONG"
        except Exception as e:
            logger.debug(f"ClamAV not available: {e}")
            return False

    def scan_bytes(self, data: bytes) -> Tuple[ScanStatus, Optional[str]]:
        """
        Scan bytes for malware.

        Args:
            data: Data to scan

        Returns:
            Tuple of (ScanStatus, threat_name or None)

        Raises:
            ScannerUnavailableError: If scanner is not available
            MalwareDetectedError: If malware is detected
        """
        if not self._enabled:
            return ScanStatus.SKIPPED, None

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._timeout)
            sock.connect((self._host, self._port))

            sock.send(b"zINSTREAM\0")

            offset = 0
            while offset < len(data):
                chunk = data[offset : offset + self.CHUNK_SIZE]
                size = struct.pack("!I", len(chunk))
                sock.send(size + chunk)
                offset += len(chunk)

            sock.send(struct.pack("!I", 0))

            response = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk
                if b"\0" in response:
                    break

            sock.close()

            return self._parse_response(response)
        except socket.timeout:
            raise ScannerUnavailableError("Scanner timed out")
        except socket.error as e:
            raise ScannerUnavailableError(f"Scanner connection failed: {e}")
        except Exception as e:
            logger.error(f"Scan failed: {e}")
            raise ScannerError(f"Scan failed: {e}")

    def scan_file(self, file_path: str) -> Tuple[ScanStatus, Optional[str]]:
        """
        Scan file for malware.

        Args:
            file_path: Path to file to scan

        Returns:
            Tuple of (ScanStatus, threat_name or None)
        """
        if not self._enabled:
            return ScanStatus.SKIPPED, None

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._timeout)
            sock.connect((self._host, self._port))

            command = f"zSCAN {file_path}\0".encode()
            sock.send(command)

            response = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk
                if b"\0" in response:
                    break

            sock.close()

            return self._parse_response(response)
        except socket.timeout:
            raise ScannerUnavailableError("Scanner timed out")
        except socket.error as e:
            raise ScannerUnavailableError(f"Scanner connection failed: {e}")
        except Exception as e:
            logger.error(f"Scan failed: {e}")
            raise ScannerError(f"Scan failed: {e}")

    def _parse_response(self, response: bytes) -> Tuple[ScanStatus, Optional[str]]:
        """Parse ClamAV response."""
        response_str = response.decode("utf-8", errors="ignore").strip("\0").strip()

        if not response_str:
            return ScanStatus.ERROR, "Empty response from scanner"

        if response_str.endswith("OK"):
            return ScanStatus.CLEAN, None

        if "FOUND" in response_str:
            parts = response_str.rsplit(":", 1)
            if len(parts) == 2:
                threat = parts[1].strip().replace(" FOUND", "")
                return ScanStatus.INFECTED, threat
            return ScanStatus.INFECTED, "Unknown threat"

        if "ERROR" in response_str:
            return ScanStatus.ERROR, response_str

        return ScanStatus.ERROR, f"Unknown response: {response_str}"

    def get_version(self) -> Optional[str]:
        """Get ClamAV version."""
        if not self._enabled:
            return None

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((self._host, self._port))
            sock.send(b"VERSION\n")
            response = sock.recv(1024)
            sock.close()
            return response.decode("utf-8", errors="ignore").strip()
        except Exception:
            return None

    def reload_database(self) -> bool:
        """Reload ClamAV virus database."""
        if not self._enabled:
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(30)
            sock.connect((self._host, self._port))
            sock.send(b"RELOAD\n")
            response = sock.recv(1024)
            sock.close()
            return b"RELOADING" in response
        except Exception as e:
            logger.error(f"Failed to reload database: {e}")
            return False
