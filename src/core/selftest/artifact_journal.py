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

# The journal is a JSON file on disk. Anything that can write to the data
# directory can forge or replay entries, so reconciliation treats it as fully
# untrusted: only the two artifact types the suite actually records may drive
# privileged SQL, and every statement is additionally scoped to selftest-owned
# accounts (see ``reconcile``).
_RECONCILABLE_TYPES = frozenset({"permission_grant", "elevated_account"})


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
        # The journal records privileged artifact metadata (elevated selftest
        # usernames and granted wildcard permissions). Keep it unreadable to
        # other local users where the platform honours POSIX modes.
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, self.path)

    # -- recording ---------------------------------------------------

    def record(self, entry_type: str, **detail: Any) -> None:
        """Persist an artifact entry BEFORE the side effect happens."""
        entry = {"type": entry_type, "recorded_at": int(time.time())}
        entry.update(detail)
        self._entries.append(entry)
        try:
            self._flush()
        except Exception as flush_error:
            # The journal is diagnostic output for the harness; keep reporting.
            import utils.logger as logger

            logger.debug(f"artifact journal flush failed: {flush_error}")

    def pending(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries = []
        try:
            self._flush()
        except Exception as flush_error:
            # The journal is diagnostic output for the harness; keep reporting.
            import utils.logger as logger

            logger.debug(f"artifact journal flush failed: {flush_error}")

    # -- reconciliation ----------------------------------------------

    def reconcile(self, db: Any) -> int:
        """Revoke/clean every recorded artifact. Returns statements executed.

        The journal is untrusted disk state, so two independent controls keep a
        stale or forged entry from harming a real account:

        * only well-formed records for the artifact types the suite creates
          may drive any SQL at all (junk is dropped, never replayed);
        * every statement is additionally scoped to selftest-owned accounts --
          notably the role-assignment delete, which previously keyed only on a
          journal-supplied id and could strip a real admin's roles if that id
          was forged or recycled by a later account.

        An entry is removed from the journal only once *all* of its statements
        succeeded. A half-applied revoke stays pending and is retried on the
        next run instead of being silently forgotten, which used to leave
        wildcard grants / elevated accounts behind after a partial failure.
        """
        if db is None:
            return 0
        touched = 0
        retained: List[Dict[str, Any]] = []
        changed = False
        for entry in self._entries:
            entry_type = entry.get("type")
            user_id = entry.get("user_id")
            if (
                entry_type not in _RECONCILABLE_TYPES
                or not isinstance(user_id, int)
                or isinstance(user_id, bool)
                or user_id <= 0
            ):
                # Malformed or tampered record: discard it without executing
                # privileged SQL. Never replay it on a future run.
                changed = True
                continue

            statements = (
                (
                    "UPDATE auth_users SET permissions = '{}' "
                    "WHERE id = ? AND username LIKE 'selftest_%'",
                    (user_id,),
                ),
                (
                    "DELETE FROM admin_role_assignments WHERE admin_id = ? "
                    "AND (admin_id IN (SELECT id FROM auth_users "
                    "WHERE username LIKE 'selftest_%') "
                    "OR admin_id IN (SELECT id FROM admin_users "
                    "WHERE username LIKE 'selftest_%'))",
                    (user_id,),
                ),
                (
                    "DELETE FROM admin_users WHERE id = ? "
                    "AND username LIKE 'selftest_%'",
                    (user_id,),
                ),
            )
            entry_ok = True
            for statement, params in statements:
                try:
                    db.execute(statement, params)
                    touched += 1
                except Exception as touch_error:
                    entry_ok = False
                    import utils.logger as logger

                    logger.debug(f"artifact journal touch failed: {touch_error}")
            if entry_ok:
                changed = True
            else:
                retained.append(entry)

        if changed:
            self._entries = retained
            try:
                self._flush()
            except Exception as flush_error:
                import utils.logger as logger

                logger.debug(f"artifact journal flush failed: {flush_error}")
        return touched
