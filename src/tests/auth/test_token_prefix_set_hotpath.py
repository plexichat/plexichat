"""Regression: the token-prefix vocabulary is a module-level frozen constant.

``parse_token()`` runs on every authenticated request (via
``SessionManager._verify_token`` and friends). It used to rebuild the
four-element ``{"bot", "email", "2fa", "wst"}`` set on every call. The accepted
prefixes are a fixed vocabulary, so they now live in a module-level
``frozenset`` that is never allocated per call.
"""

import dis
import sys

from src.core.auth import tokens as token_mod

_BUILD_SET = dis.opmap["BUILD_SET"]


def _count_build_set_in_parse_token(calls: int, token: str) -> int:
    """Count ``BUILD_SET`` opcodes executed inside ``parse_token``.

    A set literal compiles to a ``BUILD_SET`` instruction, so this is a direct
    allocation/call-count probe for the per-call vocabulary rebuild.
    """
    fn = token_mod.parse_token
    # Warm up so opcode tracing is active for every measured call.
    for _ in range(8):
        fn(token)
    total = 0

    def tracer(frame, event, arg):
        nonlocal total
        if frame.f_code is fn.__code__:
            if event == "call":
                frame.f_trace_opcodes = True
            elif (
                event == "opcode" and frame.f_code.co_code[frame.f_lasti] == _BUILD_SET
            ):
                total += 1
        return tracer

    sys.settrace(tracer)
    try:
        for _ in range(calls):
            fn(token)
    finally:
        sys.settrace(None)
    return total


def test_parse_token_does_not_rebuild_prefix_set_each_call():
    """No set is constructed on the per-request token parse hot path."""
    # A well-formed prefixed token so the prefix branch is actually exercised.
    assert _count_build_set_in_parse_token(200, "bot.1.AAAAAAAAAAAA") == 0


def test_prefix_vocabulary_is_a_frozen_module_constant():
    """The accepted prefixes live in one immutable module-level frozenset."""
    assert isinstance(token_mod._PREFIXED_TOKEN_TYPES, frozenset)
    assert token_mod._PREFIXED_TOKEN_TYPES == frozenset({"bot", "email", "2fa", "wst"})
