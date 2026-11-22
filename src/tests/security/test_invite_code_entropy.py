"""Invite codes are bearer credentials: they must carry >= 128 bits of entropy.

The invite code is the secret that grants server membership.  The historical
generator drew only 8 (or 12) characters from the 62-symbol alphanumeric
alphabet via ``secrets.choice`` -- 47.6 / 71.5 bits -- which is brute-forceable
online against the join endpoint.  These tests lock in a 128-bit floor and the
use of a CSPRNG byte source (``secrets.token_urlsafe``) that cannot silently
regress to a short/sequential/id-derived scheme.
"""

from __future__ import annotations

import ast
import inspect
import math
import time
from pathlib import Path

import pytest

from src.core.plexijoin.validation import (
    MAX_JOIN_CODE_LENGTH,
    MIN_JOIN_CODE_LENGTH,
    validate_join_code,
)
from src.core.servers.handlers.members.invites import MemberInviteMixin

pytestmark = [pytest.mark.security, pytest.mark.unit]

_URLSAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
# urlsafe base64 draws from a 64-symbol alphabet => 6 bits of entropy per char.
_BITS_PER_URLSAFE_CHAR = math.log2(64)


class _FakeDB:
    """Minimal DB that treats every issued code as a UNIQUE(code) collision."""

    def __init__(self):
        self.seen: set[str] = set()

    def fetch_one(self, sql, params):
        code = params[0]
        if code in self.seen:
            return 1
        self.seen.add(code)
        return None


class _FakeManager:
    def __init__(self, invite_code_length=8):
        self._config = {"invite_code_length": invite_code_length}


def _generator(invite_code_length=8):
    handler = MemberInviteMixin()
    handler.manager = _FakeManager(invite_code_length)
    handler.db = _FakeDB()
    return handler


def _entropy_bits(code: str) -> float:
    return len(code) * _BITS_PER_URLSAFE_CHAR


def test_invite_code_meets_128_bit_entropy_floor():
    """A freshly minted code must be >= 128 bits and acceptably shaped."""
    code = _generator().generate_invite_code()
    assert set(code) <= _URLSAFE, "code must use the urlsafe alphabet"
    assert _entropy_bits(code) >= 128, (
        f"invite code carries only {_entropy_bits(code):.0f} bits (< 128)"
    )
    assert MIN_JOIN_CODE_LENGTH <= len(code) <= MAX_JOIN_CODE_LENGTH
    assert validate_join_code(code) == code


def test_small_configured_length_cannot_weaken_the_floor():
    """A misconfigured/legacy short length must never produce a weak code."""
    for configured_length in (1, 6, 8, 12, 16):
        code = _generator(configured_length).generate_invite_code()
        assert _entropy_bits(code) >= 128, (
            f"invite_code_length={configured_length} produced a "
            f"{_entropy_bits(code):.0f}-bit code"
        )


def test_invite_codes_are_unique_across_many_draws():
    """CSPRNG output must not repeat or collapse into a small code space."""
    handler = _generator()
    codes = {handler.generate_invite_code() for _ in range(2000)}
    assert len(codes) == 2000


def _generator_source() -> str:
    """Read the generator's source segment straight from disk.

    ``inspect.getsource`` keys off ``co_firstlineno`` and can return stale
    fragments when bytecode/source line numbers are out of sync (e.g. a file
    rewritten by another process). Parsing the file with ``ast`` avoids that.
    """
    path = Path(inspect.getfile(MemberInviteMixin))
    last = ""
    for _ in range(6):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "generate_invite_code"
                ):
                    return ast.get_source_segment(text, node) or ""
            return ""
        except (OSError, SyntaxError) as exc:  # transient concurrent rewrite
            last = str(exc)
            time.sleep(0.05)
    raise AssertionError(f"could not read generator source: {last}")


def test_generator_uses_token_urlsafe_not_short_secrets_choice():
    """Static ratchet: the generator must draw from token_urlsafe(16+)."""
    source = _generator_source()
    assert source, "generate_invite_code source not found"
    assert "secrets.token_urlsafe" in source
    assert "secrets.choice" not in source
    # The random module must never back a bearer credential.
    assert "random" not in source


def test_entropy_detector_is_not_vacuous():
    """The floor check distinguishes the old weak scheme from the new one."""
    assert _entropy_bits("A" * 8) < 128  # 48 bits: old 8-char generator
    assert _entropy_bits("A" * 12) < 128  # 72 bits: old 12-char generator
    assert _entropy_bits("A" * 22) >= 128  # 132 bits: token_urlsafe(16)
