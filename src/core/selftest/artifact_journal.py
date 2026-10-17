"""Persistent artifact journal for self-test privileged state.

Privilege grants and elevated test accounts created mid-suite must not
outlive the suite. Cleanup normally removes them, but a crash between
grant and cleanup leaves escalated state behind in the target database.

Every grant/account creation is therefore recorded in a persistent
JSON journal *before* it happens; the next self-test run reconciles
the journal during pre-test cleanup (revoking recorded privileges,
removing recorded elevated accounts) and only then clears it.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional


_DEFAULT_PATH = os.path.join("data", "selftest_artifact_journal.json")


class ArtifactJournal:
    """Crash-safe record of privileged test artifacts awaiting cleanup."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _DEFAULT_PATH
        self._entries: List[Dict[str, Any]] = []
        self._load()

    # -- persistence -------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self._entries = [e for e in data if isinstance(e, dict)]
        except FileNotFoundError:
            self._entries = []
        except Exception:
            # A corrupt journal must never block a run; treat as empty.
            self._entries = []

    def _flush(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(self._entries, fh, indent=2)
        os.replace(tmp_path, self.path)

    # -- recording ---------------------------------------------------

    def record(self, entry_type: str, **detail: Any) -> None:
        """Persist an artifact entry BEFORE the side effect happens."""
        entry = {"type": entry_type, "recorded_at": int(time.time())}
        entry.update(detail)
        self._entries.append(entry)
        try:
            self._flush()
        except Exception:
            pass

    def pending(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries = []
        try:
            self._flush()
        except Exception:
            pass

    # -- reconciliation ----------------------------------------------

    def reconcile(self, db: Any) -> int:
        """Revoke/clean every recorded artifact. Returns rows touched.

        All statements are scoped to selftest-owned accounts so a stale
        journal entry can never mutate real users.
        """
        if db is None:
            return 0
        touched = 0
        for entry in self._entries:
            user_id = entry.get("user_id")
            if user_id is None:
                continue
            for statement, params in (
                (
                    "UPDATE auth_users SET permissions = '{}' "
                    "WHERE id = ? AND username LIKE 'selftest_%'",
                    (user_id,),
                ),
                (
                    "DELETE FROM admin_role_assignments WHERE admin_id = ?",
                    (user_id,),
                ),
                (
                    "DELETE FROM admin_users WHERE id = ? "
                    "AND username LIKE 'selftest_%'",
                    (user_id,),
                ),
            ):
                try:
                    db.execute(statement, params)
                    touched += 1
                except Exception:
                    continue
        if touched:
            self.clear()
        return touched
