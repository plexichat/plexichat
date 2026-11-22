"""Command/option names must reject Unicode confusables, not just match ``\\w``.

Python's ``\\w`` matches every Unicode word character, so the old
``^\\w{1,32}$`` patterns accepted fullwidth and Cyrillic lookalikes of ASCII
command names.  ``name == name.lower()`` did not help because most confusables
are already lowercase.  A fullwidth ``cmdn`` is stored verbatim while a client
that NFKC-normalizes renders it as ``cmd``, so two visually identical commands
(or a lookalike of a protected name such as ``admin``) can coexist and slip past
raw-string duplicate/allow-list checks.

This module pins the fail-closed contract: names are ASCII-only, NFKC-stable,
and fully matched (no trailing newline).
"""

import unicodedata

from src.core.applications.commands.options import validate_option
from src.core.applications.commands.validation import validate_command_name
from src.core.applications.models import CommandType


def _chat(name):
    return validate_command_name(name, CommandType.CHAT_INPUT)


def test_fullwidth_lookalike_command_name_rejected():
    # FULLWIDTH LATIN SMALL LETTER C/M/D -> NFKC "cmd"
    lookalike = "\uff43\uff4d\uff44"
    assert unicodedata.normalize("NFKC", lookalike) == "cmd"
    assert lookalike != "cmd"
    ok, issues = _chat(lookalike)
    assert ok is False
    assert issues  # non-vacuous: a concrete rejection reason


def test_cyrillic_lookalike_mimicking_admin_rejected():
    # CYRILLIC SMALL LETTER A + "dm" + DOTLESS I + "n" -> renders "admin"
    spoof = "\u0430dm\u0131n"
    ok, issues = _chat(spoof)
    assert ok is False
    assert issues


def test_kelvin_sign_and_long_s_lookalikes_rejected():
    # KELVIN SIGN NFKC-folds to "K"; LATIN SMALL LETTER LONG S to "s".
    # Pass the raw submitted name: the registry lowercases only *after* this
    # validation, so the validator must reject the non-ASCII form itself.
    for spoof in ("\u212acmd", "ad\u017fn"):
        ok, issues = _chat(spoof)
        assert ok is False
        assert issues


def test_ascii_lowercase_command_name_still_accepted():
    ok, issues = _chat("my-command_1")
    assert ok is True, issues


def test_trailing_newline_rejected_by_fullmatch():
    # ``$`` matches before a trailing newline; only fullmatch closes this.
    ok, issues = _chat("cmd\n")
    assert ok is False
    assert issues


def test_context_menu_confusable_rejected():
    # FULLWIDTH LATIN CAPITAL LETTER A + "dmin" -> NFKC "Admin"
    ok, issues = validate_command_name("\uff21dmin", CommandType.USER)
    assert ok is False
    assert issues


def test_option_name_confusable_rejected():
    option = {"name": "\uff43\uff4d\uff44", "description": "d", "type": 3}
    ok, issues = validate_option(option)
    assert ok is False
    assert any("name" in issue for issue in issues)


def test_ascii_option_name_still_accepted():
    option = {"name": "user_id", "description": "d", "type": 3}
    ok, issues = validate_option(option)
    assert ok is True, issues
