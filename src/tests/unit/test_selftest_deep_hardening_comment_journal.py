"""Regressions for two deep selftest hardening invariants.

1. The non-destructive denylist must normalize SQL comments as whitespace:
   a comment used as a keyword separator (or to fake a WHERE clause) must not
   let an unscoped whole-table wipe through.
2. The artifact journal must never write through a predictable ``.tmp`` path
   (symlink hijack) and must reject a traversal path at the filesystem sink.
"""

import json

import pytest


@pytest.mark.unit
@pytest.mark.parametrize(
    "statement",
    [
        "DELETE/**/FROM/**/auth_users",
        "DELETE/*x*/FROM/*x*/auth_users;",
        "DELETE /* c */ FROM auth_users",
        "DELETE -- c\nFROM auth_users",
        "DELETE FROM/* c */auth_users -- looks scoped?",
        "DELETE FROM t /* WHERE id = ? */",
    ],
)
def test_comment_separators_cannot_hide_destructive_sql(statement):
    from src.core.selftest.db_guard import assert_non_destructive

    with pytest.raises(ValueError):
        assert_non_destructive(statement)


@pytest.mark.unit
def test_scoped_delete_with_comment_text_in_literal_still_accepted():
    """Comment-shaped text inside a literal must not disable scoped deletes."""
    from src.core.selftest.db_guard import assert_non_destructive

    assert_non_destructive(
        "DELETE FROM auth_users WHERE note = 'a -- b /* c */' AND id = ?"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_path",
    [
        "../../etc/passwd",
        "data/../../../etc/passwd",
        "/etc/../etc/passwd",
        "data/journal\x00.json",
    ],
)
def test_journal_rejects_traversal_paths(bad_path):
    from src.core.selftest.artifact_journal import ArtifactJournal, _DEFAULT_PATH

    assert ArtifactJournal(bad_path).path == _DEFAULT_PATH


@pytest.mark.unit
def test_journal_flush_ignores_planted_tmp_symlink(tmp_path):
    """A planted ``<journal>.tmp`` symlink must not redirect the write.

    The old writer used a predictable ``path + ".tmp"`` name with
    ``open(..., "w")``, which follows symlinks. An attacker with write access
    to the data directory could redirect the next flush to an arbitrary file,
    overwrite it with journal JSON, and reset its mode to 0o600.
    """
    from src.core.selftest.artifact_journal import ArtifactJournal

    journal_path = tmp_path / "journal.json"
    victim = tmp_path / "victim.txt"
    victim.write_text("DO NOT TOUCH", encoding="utf-8")

    planted = tmp_path / "journal.json.tmp"
    planted.symlink_to(victim)

    journal = ArtifactJournal(str(journal_path))
    journal.record("permission_grant", user_id=7, permissions=["admin.*"])

    assert victim.read_text(encoding="utf-8") == "DO NOT TOUCH"
    assert planted.is_symlink()
    entries = json.loads(journal_path.read_text(encoding="utf-8"))
    assert entries[0]["user_id"] == 7
