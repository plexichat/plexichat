import os
import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Tuple, List

try:
    import fcntl
except ImportError:
    fcntl = None

import utils.config as config
import utils.logger as logger


class DSARLog:
    """
    Hash-chained append-only audit log for DSAR operations.
    Ensures GDPR compliance and provides tamper-evidence.
    """

    def __init__(self, file_path: Optional[str] = None):
        cfg = config.get("dsar.audit_log", {})
        self.file_path = file_path or cfg.get(
            "file_path",
            str(Path.home() / ".plexichat" / "data" / "dsar_audit_log.jsonl"),
        )
        self.hash_chain_enabled = cfg.get("hash_chain_enabled", True)
        self._ensure_dir()

    def _ensure_dir(self):
        directory = os.path.dirname(self.file_path)
        if directory and not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to create audit log directory {directory}: {e}")

    def _get_last_hash(self) -> str:
        """Retrieve the hash of the last entry to continue the chain."""
        if not os.path.exists(self.file_path) or os.path.getsize(self.file_path) == 0:
            return "0" * 64

        try:
            with open(self.file_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                pos = f.tell()
                buffer = b""
                while pos > 0:
                    pos -= 1
                    f.seek(pos)
                    char = f.read(1)
                    if char == b"\n" and buffer:
                        break
                    buffer = char + buffer

                if not buffer:
                    return "0" * 64

                last_line = json.loads(buffer.decode("utf-8"))
                return last_line.get("checksum", "0" * 64)
        except Exception as e:
            logger.error(f"Error reading last hash from audit log: {e}")
            return "0" * 64

    def log_event(
        self,
        user_id: int,
        action: str,
        identifier: str,
        metadata: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Log a DSAR event (REQUESTED, APPROVED, DENIED, GENERATING, READY, DOWNLOADED, EXPIRED, FAILED, CANCELLED).
        Returns the checksum of the entry.
        """
        id_hash = hashlib.sha256(identifier.encode()).hexdigest()

        entry = {
            "timestamp": int(time.time()),
            "user_id": user_id,
            "identifier_hash": id_hash,
            "action": action,
            "metadata": metadata or {},
        }

        try:
            with open(self.file_path, "a+") as f:
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_EX)  # type: ignore

                try:
                    prev_hash = self._get_last_hash()
                    entry["prev_hash"] = prev_hash

                    content = json.dumps(entry, sort_keys=True)
                    checksum = hashlib.sha256(
                        (prev_hash + content).encode()
                    ).hexdigest()
                    entry["checksum"] = checksum

                    f.write(json.dumps(entry) + "\n")
                    f.flush()
                    os.fsync(f.fileno())

                    logger.info(
                        f"DSAR Audit Log: {action} for user {user_id} (Checksum: {checksum[:8]}...)"
                    )
                    return checksum
                finally:
                    if fcntl:
                        fcntl.flock(f, fcntl.LOCK_UN)  # type: ignore
        except Exception as e:
            logger.error(f"Failed to write to audit log: {e}")
            return None

    def verify_chain(self) -> Tuple[bool, int, Optional[str]]:
        """
        Verifies the integrity of the entire hash chain.
        Returns (is_valid, record_count, error_message).
        """
        if not os.path.exists(self.file_path):
            return True, 0, None

        count = 0
        expected_prev_hash = "0" * 64

        try:
            with open(self.file_path, "r") as f:
                for line_num, line in enumerate(f, 1):
                    if not line.strip():
                        continue

                    entry = json.loads(line)
                    stored_checksum = entry.pop("checksum", None)
                    stored_prev_hash = entry.get("prev_hash")

                    if stored_prev_hash != expected_prev_hash:
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: prev_hash mismatch",
                        )

                    content = json.dumps(entry, sort_keys=True)
                    calculated_checksum = hashlib.sha256(
                        (stored_prev_hash + content).encode()
                    ).hexdigest()

                    if calculated_checksum != stored_checksum:
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: checksum invalid",
                        )

                    expected_prev_hash = stored_checksum
                    count += 1

            return True, count, None
        except Exception as e:
            return False, count, str(e)

    def get_requests_by_status(self, status: str) -> List[Dict]:
        """
        Reads the log to find requests with a specific status.
        """
        results = []
        if not os.path.exists(self.file_path):
            return results

        try:
            with open(self.file_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry.get("action") == status:
                        results.append(entry)
            return results
        except Exception as e:
            logger.error(f"Failed to read requests from log: {e}")
            return results
