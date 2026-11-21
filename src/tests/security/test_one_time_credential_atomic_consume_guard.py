"""SEC invariant: one-time credentials are consumed by an atomic single-winner claim.

A validator that checks a challenge/code/nonce/ticket/token and *then* removes it
with a plain ``cache_delete`` / ``DELETE`` has a check-then-delete TOCTOU: two
workers (or two concurrent coroutines) both observe the live credential before
either removes it, so both accept it. The one-time-credential campaign replaced
those consumes with single-winner claims:

  * ``UPDATE ... WHERE id = ? AND used = 0`` (or ``revoked = 0``,
    ``redeemed_at IS NULL``, ``backup_codes_hash = ?`` ...) plus a rowcount
    check -- the UPDATE itself decides the winner;
  * ``insert_or_ignore`` / ``SET NX`` (``set_nx``, ``nx=True``) claims;
  * a Lua GET+DEL script or a ``dict.pop`` under a lock;
  * ``BoundedReplayGuard.consume`` (and its replay-cache equivalents).

This is a pure-AST ratchet over checked-in source: no app import, no DB, no
network. It combines three layers so it cannot pass vacuously:

  1. ``ONE_TIME_SURFACES`` -- every known one-time credential validator, each
     with the atomic claim it must keep (some claims live in a named helper;
     the helper is pinned too, so deleting the claim there fails the build).
  2. ``DOCUMENTED_EXCEPTIONS`` -- naming-convention lookalikes that are *not*
     one-time (hash comparators, persistent tokens, delegating wrappers). Each
     has a written reason; an undocumented lookalike is not silently exempt.
  3. A live discovery pass over the six campaign directories: any *new*
     ``_validate_*``/``verify_*``/``_check_*`` function on a
     challenge/code/nonce/ticket/token whose only removal is a plain delete
     and which has no atomic claim fails closed.

The detector is self-tested against synthetic validators first (a
validate-with-plain-delete is flagged; every atomic shape and the documented
cases are accepted), so a detector that never fires cannot make the real scan
pass trivially.
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# The six one-time-credential campaign surfaces. ``public_license`` is a module,
# not a package, so the roots may be either directories or files.
SCAN_ROOTS = (
    "src/core/auth",
    "src/core/admin/auth",
    "src/core/applications/oauth",
    "src/core/plexijoin",
    "src/api/routes/public_license.py",
    "src/core/suite/stash",
)

# Naming convention from the campaign brief. Kept deliberately narrow (only
# the five one-time credential nouns) to avoid flagging unrelated verify_*
# helpers; the curated inventory below covers one-time surfaces whose names
# use different verbs (e.g. exchange_code, redeem_download_ticket).
NAME_PREFIXES = ("_validate_", "verify_", "_check_")
NAME_TOKENS = ("challenge", "code", "nonce", "ticket", "token")

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

# (source file, qualified name, pinned helper or None, why it is one-time).
# The helper is a (source file, qualified name) pair whose body carries the
# atomic claim when the validator itself delegates it (e.g. the admin OTP
# challenge claim, or the TOTP replay cache).
ONE_TIME_SURFACES = (
    (
        "src/core/auth/oauth/state.py",
        "OAuthStateManager.verify_state",
        None,
        "OAuth state consumed by conditional UPDATE used=0 + rowcount",
    ),
    (
        "src/core/auth/managers/registration.py",
        "RegistrationMixin.verify_email",
        None,
        "email verification token consumed by single-use CAS",
    ),
    (
        "src/core/auth/managers/password.py",
        "PasswordMixin.reset_password",
        None,
        "password reset token consumed by single-use CAS",
    ),
    (
        "src/core/auth/managers/twofactor.py",
        "TwoFactorMixin.complete_2fa",
        None,
        "2FA challenge and backup code consumed by conditional CAS",
    ),
    (
        "src/core/auth/managers/session/tickets.py",
        "TicketMixin._verify_ws_ticket",
        None,
        "gateway ticket consumed by UPDATE used=0 + rowcount",
    ),
    (
        "src/core/auth/managers/session/lifecycle.py",
        "LifecycleMixin._verify_ws_ticket",
        None,
        "gateway ticket consumed by UPDATE used=0 + rowcount (mixin twin)",
    ),
    (
        "src/core/auth/managers/session/lifecycle.py",
        "LifecycleMixin.refresh_session",
        None,
        "pre-rotation refresh token claimed by refresh_replay_guard.consume",
    ),
    (
        "src/core/auth/passkeys.py",
        "PasskeyManager.verify_registration",
        None,
        "WebAuthn registration challenge consumed by single-use CAS",
    ),
    (
        "src/core/auth/passkeys.py",
        "PasskeyManager.verify_authentication",
        None,
        "WebAuthn authentication challenge consumed by single-use CAS",
    ),
    (
        "src/core/auth/totp.py",
        "verify_totp_code",
        ("src/core/auth/totp.py", "TOTPReplayCache.check_and_mark"),
        "TOTP code replay-claimed by insert_or_ignore / SET NX / bounded guard",
    ),
    (
        "src/core/admin/auth/helpers.py",
        "_validate_otp_challenge",
        ("src/core/admin/auth/helpers.py", "_claim_otp_challenge_once"),
        "admin OTP challenge claimed by SET NX / BoundedReplayGuard.consume",
    ),
    (
        "src/core/admin/auth/otp_mixin.py",
        "OTPMixin.verify_otp_setup",
        None,
        "admin OTP setup code consumed by conditional CAS",
    ),
    (
        "src/core/admin/auth/otp_mixin.py",
        "OTPMixin.verify_otp",
        None,
        "admin OTP code consumed by CAS; challenge separately claimed in "
        "_validate_otp_challenge",
    ),
    (
        "src/core/admin/auth/otp_setup_mixin.py",
        "OTPSetupMixin.disable_otp",
        None,
        "admin OTP disable code consumed by conditional CAS",
    ),
    (
        "src/core/applications/oauth/flows.py",
        "OAuth2Flow.exchange_code",
        None,
        "OAuth authorization code consumed by UPDATE used=0 + rowcount",
    ),
    (
        "src/core/applications/oauth/flows.py",
        "OAuth2Flow.refresh_token",
        None,
        "refresh token rotated via UPDATE revoked=0 + rowcount",
    ),
    (
        "src/core/plexijoin/manager.py",
        "PlexiJoinManager.consume_replay_nonce",
        None,
        "federation replay nonce claimed by insert_or_ignore",
    ),
    (
        "src/api/routes/public_license.py",
        "NonceStore.consume",
        None,
        "license challenge nonce consumed by Lua GET+DEL / locked dict.pop",
    ),
    (
        "src/api/routes/public_license.py",
        "export_license",
        ("src/api/routes/public_license.py", "NonceStore.consume"),
        "signed challenge consumed through NonceStore.consume",
    ),
    (
        "src/core/suite/stash/stash_manager_items/tickets.py",
        "ItemTicketMixin.redeem_download_ticket",
        None,
        "stash download ticket consumed by redeemed_at IS NULL CAS",
    ),
)

# Naming-convention lookalikes that are not one-time credentials. A reason is
# mandatory: an undocumented entry cannot silence a real gap.
DOCUMENTED_EXCEPTIONS = (
    (
        "src/core/auth/totp.py",
        "verify_backup_code",
        "Pure hash comparator; the indexed backup hash is removed by the "
        "conditional backup_codes_hash CAS in TwoFactorMixin.complete_2fa "
        "(pinned above)",
    ),
    (
        "src/core/auth/registration.py",
        "verify_email",
        "Thin module wrapper delegating to RegistrationMixin.verify_email "
        "(pinned above)",
    ),
    (
        "src/core/auth/passkeys.py",
        "verify_passkey_registration",
        "Module wrapper delegating to PasskeyManager.verify_registration "
        "(pinned above)",
    ),
    (
        "src/core/auth/passkeys.py",
        "verify_passkey_authentication",
        "Module wrapper delegating to PasskeyManager.verify_authentication "
        "(pinned above)",
    ),
    (
        "src/core/auth/managers/twofactor.py",
        "TwoFactorMixin.confirm_2fa",
        "Validates the setup code through verify_totp_code (pinned above); "
        "the revoked = 0 updates are post-success session revocation",
    ),
    (
        "src/core/auth/managers/twofactor.py",
        "TwoFactorMixin.disable_2fa",
        "Validates the disable code through verify_totp_code (pinned above); "
        "the revoked = 0 updates are post-success session revocation",
    ),
    (
        "src/core/auth/managers/passkey.py",
        "PasskeyMixin.verify_passkey_registration",
        "Delegates to PasskeyManager.verify_registration (pinned above)",
    ),
    (
        "src/core/auth/managers/passkey.py",
        "PasskeyMixin.verify_passkey_authentication",
        "Delegates to PasskeyManager.verify_authentication (pinned above)",
    ),
    (
        "src/core/auth/managers/api_token.py",
        "ApiTokenMixin.verify_api_access_token",
        "Persistent, revocable API token; not a single-use credential",
    ),
    (
        "src/core/auth/tokens.py",
        "verify_api_access_token",
        "Persistent, revocable API token; not a single-use credential",
    ),
    (
        "src/core/auth/tokens.py",
        "verify_token_hash",
        "Constant-time hash comparison primitive, no credential removal",
    ),
    (
        "src/core/auth/sessions.py",
        "verify_token",
        "Session/bot token verification; revocable but not single-use",
    ),
    (
        "src/core/auth/managers/session/verification.py",
        "VerificationMixin.verify_token",
        "Session/bot token verification; revocable but not single-use",
    ),
    (
        "src/core/auth/managers/session/lifecycle.py",
        "LifecycleMixin.verify_token",
        "Session/bot token verification; revocable but not single-use",
    ),
    (
        "src/core/auth/oauth/state.py",
        "OAuthStateManager.verify_nonce",
        "OIDC nonce is hash-compared to the single-use state; the one-time "
        "consume is OAuthStateManager.verify_state (pinned above)",
    ),
    (
        "src/core/auth/oauth/state.py",
        "verify_oauth_nonce",
        "Module wrapper around the OIDC nonce hash comparison; the state "
        "consume is pinned above",
    ),
    (
        "src/core/auth/oauth/state.py",
        "verify_oauth_state",
        "Convenience wrapper around OAuthStateManager.verify_state (pinned above)",
    ),
    (
        "src/core/admin/auth/__init__.py",
        "verify_otp",
        "Re-export delegating to OTPMixin.verify_otp (pinned above)",
    ),
    (
        "src/core/admin/auth/__init__.py",
        "verify_otp_setup",
        "Re-export delegating to OTPMixin.verify_otp_setup (pinned above)",
    ),
    (
        "src/core/applications/oauth/flows.py",
        "OAuth2Flow.verify_access_token",
        "Persistent bearer token; not a single-use credential",
    ),
    (
        "src/core/applications/oauth/tokens.py",
        "verify_token_hash",
        "Constant-time hash comparison primitive, no credential removal",
    ),
    (
        "src/core/applications/oauth/tokens.py",
        "verify_pkce_challenge",
        "PKCE verifier is hash-compared; the single-use authority is the "
        "authorization code consumed by exchange_code (pinned above)",
    ),
)


# ---------------------------------------------------------------------------
# Source loading / AST helpers
# ---------------------------------------------------------------------------


def _source_files():
    files = []
    for root in SCAN_ROOTS:
        path = ROOT / root
        if path.is_file():
            files.append(root)
        elif path.is_dir():
            files.extend(
                sorted(p.relative_to(ROOT).as_posix() for p in path.rglob("*.py"))
            )
    return tuple(files)


SOURCE_FILES = _source_files()
_MODULES = {}


def _module(relative):
    if relative not in _MODULES:
        _MODULES[relative] = ast.parse(
            (ROOT / relative).read_text(encoding="utf-8"), filename=relative
        )
    return _MODULES[relative]


def _literal_text(node):
    """Return the value of a plain (possibly implicitly concatenated) string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _module_strings(tree):
    """Map module-level string constants to their values (SQL, Lua scripts)."""
    out = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = _literal_text(node.value)
        if value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = value
    return out


def _own_nodes(root):
    """Yield ``root`` and its live-body nodes; skip nested defs and lambdas.

    Defining a helper that performs an atomic claim does not execute it. As
    with the other static guards in this suite, dead nested evidence must not
    protect a validator; calling a named helper is handled by the pinned-helper
    inventory instead.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _strings_in_scope(function, module_strings):
    """String literals in the live body plus referenced module constants."""
    nodes = list(_own_nodes(function))
    known = dict(module_strings)
    found = set()
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = _literal_text(node.value)
            if value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if isinstance(target, ast.Name):
                    known[target.id] = value
    referenced = {n.id for n in nodes if isinstance(n, ast.Name)}
    found.update(value for name, value in known.items() if name in referenced)
    return found


def _call_ident(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _call_source(call):
    return ast.unparse(call.func)


def _find(relative, qualified_name):
    """Find a module function or a ``Class.method`` in a parsed module."""
    tree = _module(relative)
    parts = qualified_name.split(".")
    if len(parts) == 1:
        return next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, FUNC_TYPES) and node.name == parts[0]
            ),
            None,
        )
    owner = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == parts[0]
        ),
        None,
    )
    if owner is None:
        return None
    return next(
        (
            node
            for node in owner.body
            if isinstance(node, FUNC_TYPES) and node.name == parts[1]
        ),
        None,
    )


def _discover():
    """All naming-convention one-time-validator candidates in the scan roots."""
    out = []
    for relative in SOURCE_FILES:
        for node in ast.walk(_module(relative)):
            if not isinstance(node, FUNC_TYPES):
                continue
            name = node.name.lower()
            if not name.startswith(NAME_PREFIXES):
                continue
            if not any(token in name for token in NAME_TOKENS):
                continue
            out.append((relative, node.name, node))
    return out


DISCOVERED = _discover()


# ---------------------------------------------------------------------------
# Atomic-claim / plain-delete detectors
# ---------------------------------------------------------------------------


def _is_cas_update(text):
    """A conditional UPDATE: a WHERE predicate beyond the row id.

    ``UPDATE t SET used = 1 WHERE id = ?`` is not single-winner -- two racers
    both update one row. A second conjunct (``AND used = 0``,
    ``AND redeemed_at IS NULL``, ``AND backup_codes_hash = ?``), a NULL/range
    predicate, or a bare ``WHERE used = 0`` is.
    """
    low = " ".join(text.lower().split())
    if "update" not in low or " set " not in low or " where " not in low:
        return False
    where = low.split(" where ", 1)[1]
    if " and " in where:
        return True
    if any(op in where for op in (" is null", "<> ", "!= ", " > ", " < ")):
        return True
    return any(
        state in where
        for state in ("used = 0", "used=0", "revoked = 0", "revoked=0")
    )


def _has_rowcount_check(nodes):
    for node in nodes:
        if isinstance(node, ast.Attribute) and node.attr == "rowcount":
            return True
        if isinstance(node, ast.Name) and node.id == "rowcount":
            return True
        # getattr(cursor, "rowcount", 0)
        if isinstance(node, ast.Constant) and node.value == "rowcount":
            return True
    return False


def _is_lua_get_del(text):
    low = text.lower()
    if "redis.call" not in low:
        return False
    return bool(
        re.search(r"redis\.call\(\s*['\"]get", low)
        and re.search(r"redis\.call\(\s*['\"]del", low)
    )


def _locked_pop(function, nodes):
    """A ``.pop()`` executed inside ``with <something>lock...``."""
    parents = {}
    for node in nodes:
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in nodes:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "pop":
            continue
        current = node
        while current in parents:
            current = parents[current]
            if isinstance(current, ast.With) and any(
                "lock" in ast.unparse(item.context_expr).lower()
                for item in current.items
            ):
                return True
    return False


def _atomic_signals(function, module_strings=None):
    """Names of atomic single-winner primitives used by ``function``."""
    if function is None:
        return set()
    module_strings = module_strings or {}
    nodes = list(_own_nodes(function))
    strings = _strings_in_scope(function, module_strings)
    calls = [n for n in nodes if isinstance(n, ast.Call)]
    signals = set()

    if any(_is_cas_update(text) for text in strings) and _has_rowcount_check(nodes):
        signals.add("conditional-update+rowcount")
    if any("insert_or_ignore" in _call_ident(c).lower() for c in calls):
        signals.add("insert_or_ignore")
    for call in calls:
        ident = _call_ident(call).lower()
        source = _call_source(call).lower()
        if "set_nx" in ident or "setnx" in ident:
            signals.add("set-nx")
        if any(k.arg == "nx" for k in call.keywords):
            signals.add("set-nx")
        if "redis.call" in source and " nx" in source:
            signals.add("set-nx")
    if any(
        "redis.call" in text and " nx" in text.lower() and "set" in text.lower()
        for text in strings
    ):
        signals.add("set-nx")
    if any(_is_lua_get_del(text) for text in strings):
        signals.add("lua-get-del")
    if _locked_pop(function, nodes):
        signals.add("locked-dict-pop")
    for call in calls:
        ident = _call_ident(call).lower()
        source = _call_source(call).lower()
        combined = f"{source} {ident}"
        if (
            any(word in combined for word in ("guard", "replay", "claim", "nonce"))
            and any(
                word in combined
                for word in ("consume", "check_and_mark", "claim", "once")
            )
        ):
            signals.add("replay-guard-consume")
    return signals


def _plain_delete_signals(function, module_strings=None):
    """Names of non-atomic removal primitives used by ``function``."""
    if function is None:
        return set()
    module_strings = module_strings or {}
    nodes = list(_own_nodes(function))
    strings = _strings_in_scope(function, module_strings)
    signals = set()
    for node in nodes:
        if isinstance(node, ast.Call) and "cache_delete" in _call_ident(node).lower():
            signals.add("cache_delete")
    if any("delete from" in text.lower() for text in strings):
        signals.add("DELETE FROM")
    if any(isinstance(node, ast.Delete) for node in nodes):
        signals.add("del-statement")
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "pop"
        for node in nodes
    ) and not _locked_pop(function, nodes):
        signals.add("unlocked-dict-pop")
    return signals


# ---------------------------------------------------------------------------
# Checks (same functions drive the self-tests and the real tree)
# ---------------------------------------------------------------------------


def _is_documented(relative, qualified_name, documented):
    return (
        relative,
        qualified_name,
    ) in documented or (
        relative,
        qualified_name.split(".")[-1],
    ) in documented


def _surface_offender(
    relative, qualified_name, function, helper, module_strings, helper_strings, note
):
    if function is None:
        return f"{relative}:{qualified_name}: validator missing"
    signals = _atomic_signals(function, module_strings)
    if not signals and helper is not None:
        signals = _atomic_signals(helper, helper_strings)
    if not signals:
        return f"{relative}:{qualified_name}: no atomic single-winner consume ({note})"
    return None


def _new_candidate_offender(
    relative, function, known, documented, module_strings=None
):
    """Fail closed on a new one-time validator whose removal is a plain delete."""
    name = function.name
    if (relative, name) in known or (relative, name) in documented:
        return None
    deletes = _plain_delete_signals(function, module_strings or {})
    if not deletes:
        return None
    if _atomic_signals(function, module_strings or {}):
        return None
    return (
        f"{relative}:{function.lineno} {name}: new one-time validator uses "
        f"non-atomic {', '.join(sorted(deletes))} with no atomic claim"
    )


def _find_source(source, name):
    return next(
        (
            node
            for node in ast.walk(ast.parse(textwrap.dedent(source)))
            if isinstance(node, FUNC_TYPES) and node.name == name
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Self-tests: prove the detector is neither vacuous nor over-broad
# ---------------------------------------------------------------------------


def test_self_test_plain_delete_validator_is_flagged():
    bad = """
    def verify_challenge_code(token):
        cache_delete(f"challenge:{token}")
        return True
    """
    function = _find_source(bad, "verify_challenge_code")
    assert function is not None
    assert "cache_delete" in _plain_delete_signals(function)
    assert not _atomic_signals(function)
    assert _new_candidate_offender("<synthetic>", function, set(), set()) is not None


def test_self_test_every_atomic_shape_is_accepted():
    cases = {
        "conditional-update+rowcount": """
            def verify_challenge_code(token, db):
                cursor = db.execute(
                    "UPDATE challenges SET used = 1 WHERE id = ? AND used = 0",
                    (token,),
                )
                return getattr(cursor, "rowcount", 0) == 1
        """,
        "insert_or_ignore": """
            def verify_challenge_code(token, db):
                return bool(db.insert_or_ignore("claims", ["token"], (token,)))
        """,
        "set-nx": """
            def verify_challenge_code(token, client):
                return bool(client.set_nx(token, "1", ttl=60))
        """,
        "lua-get-del": """
            def verify_challenge_code(token, client):
                return bool(client.eval_lua(_CONSUME_LUA, keys=[token], args=[]))
        """,
        "locked-dict-pop": """
            def verify_challenge_code(token):
                with self._lock:
                    return self._codes.pop(token, None) is not None
        """,
        "replay-guard-consume": """
            def verify_challenge_code(token):
                return _REPLAY_GUARD.consume(token)
        """,
        "claim-helper": """
            def verify_challenge_code(token):
                return _claim_challenge_once(token)
        """,
    }
    expected_signals = {
        "conditional-update+rowcount": "conditional-update+rowcount",
        "insert_or_ignore": "insert_or_ignore",
        "set-nx": "set-nx",
        "lua-get-del": "lua-get-del",
        "locked-dict-pop": "locked-dict-pop",
        "replay-guard-consume": "replay-guard-consume",
        "claim-helper": "replay-guard-consume",
    }
    module_strings = {
        "<synthetic>": {
            "_CONSUME_LUA": (
                "local v = redis.call('GET', KEYS[1])\n"
                "redis.call('DEL', KEYS[1])\n"
                "return 1"
            )
        }
    }
    for label, source in cases.items():
        function = _find_source(source, "verify_challenge_code")
        assert function is not None, label
        signals = _atomic_signals(function, module_strings.get("<synthetic>", {}))
        assert expected_signals[label] in signals, (label, signals)
        assert _new_candidate_offender(
            "<synthetic>", function, set(), set(), module_strings.get("<synthetic>", {})
        ) is None, label


def test_self_test_conditional_update_without_rowcount_is_not_atomic():
    """A conditional UPDATE whose rowcount is ignored cannot decide a winner.

    Paired with a plain cache delete -- the legacy shape -- the validator must
    be flagged: the UPDATE's result was discarded, so the delete is the only
    thing standing between two concurrent callers and a replay.
    """
    source = """
    def verify_challenge_code(token, db):
        db.execute("UPDATE challenges SET used = 1 WHERE id = ? AND used = 0", (token,))
        cache_delete(f"challenge:{token}")
        return True
    """
    function = _find_source(source, "verify_challenge_code")
    assert "conditional-update+rowcount" not in _atomic_signals(function)
    assert _new_candidate_offender("<synthetic>", function, set(), set()) is not None


def test_self_test_documented_and_pinned_cases_are_accepted():
    bad = """
    def verify_challenge_code(token):
        cache_delete(token)
        return True
    """
    function = _find_source(bad, "verify_challenge_code")
    # Undocumented: fails closed.
    assert _new_candidate_offender("<synthetic>", function, set(), set()) is not None
    # Documented with a reason: accepted by the discovery pass.
    documented = {("<synthetic>", "verify_challenge_code")}
    assert (
        _new_candidate_offender("<synthetic>", function, set(), documented) is None
    )
    # Pinned in the inventory: its plain delete is allowed only because a
    # separate atomic claim is pinned (verified via a helper here).
    helper_source = """
    def _claim_challenge_once(token):
        return _GUARD.consume(token)
    """
    helper = _find_source(helper_source, "_claim_challenge_once")
    assert _atomic_signals(helper)
    assert (
        _surface_offender(
            "<synthetic>",
            "verify_challenge_code",
            function,
            helper,
            {},
            {},
            "documented helper claim",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Real-tree invariants
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous():
    assert len(SOURCE_FILES) >= 30, f"only {len(SOURCE_FILES)} source files scanned"
    assert len(DISCOVERED) >= 12, f"only {len(DISCOVERED)} convention matches"
    assert len(ONE_TIME_SURFACES) >= 18, "inventory shrank"
    classified = {(r, q) for r, q, _, _ in ONE_TIME_SURFACES}
    classified |= {(r, q.split(".")[-1]) for r, q, _, _ in ONE_TIME_SURFACES}
    classified |= {(r, q) for r, q, _ in DOCUMENTED_EXCEPTIONS}
    classified |= {(r, q.split(".")[-1]) for r, q, _ in DOCUMENTED_EXCEPTIONS}
    # Every discovered convention match must be classified, one way or the
    # other; an unclassified match is a future gap waiting to happen.
    missing = [
        f"{relative}:{name}"
        for relative, name, _function in DISCOVERED
        if (relative, name) not in classified
    ]
    assert not missing, f"unclassified one-time validator candidates: {missing}"
    found = sum(
        1
        for relative, qualified, _, _ in ONE_TIME_SURFACES
        if _find(relative, qualified) is not None
    )
    assert found == len(ONE_TIME_SURFACES), f"only {found} surfaces found"
    direct = sum(
        1
        for relative, qualified, _, _ in ONE_TIME_SURFACES
        if _atomic_signals(_find(relative, qualified), _module_strings(_module(relative)))
    )
    assert direct >= 15, f"only {direct} surfaces show an in-body atomic claim"


def test_documented_exceptions_are_real_and_justified():
    for relative, qualified, reason in DOCUMENTED_EXCEPTIONS:
        assert reason.strip(), f"missing reason for {relative}:{qualified}"
        assert _find(relative, qualified) is not None, (
            f"documented exception no longer exists: {relative}:{qualified}"
        )


def test_every_one_time_surface_has_an_atomic_consume():
    offenders = []
    for relative, qualified, helper, note in ONE_TIME_SURFACES:
        function = _find(relative, qualified)
        helper_function = None
        helper_strings = {}
        if helper is not None:
            helper_function = _find(helper[0], helper[1])
            helper_strings = _module_strings(_module(helper[0]))
        offender = _surface_offender(
            relative,
            qualified,
            function,
            helper_function,
            _module_strings(_module(relative)),
            helper_strings,
            note,
        )
        if offender:
            offenders.append(offender)
    assert not offenders, (
        "One-time credential validators without an atomic single-winner "
        "consume:\n  " + "\n  ".join(sorted(offenders))
    )


def test_new_one_time_validator_with_plain_delete_fails_closed():
    known = {(r, q) for r, q, _, _ in ONE_TIME_SURFACES}
    known |= {(r, q.split(".")[-1]) for r, q, _, _ in ONE_TIME_SURFACES}
    documented = {(r, q) for r, q, _ in DOCUMENTED_EXCEPTIONS}
    documented |= {(r, q.split(".")[-1]) for r, q, _ in DOCUMENTED_EXCEPTIONS}
    offenders = []
    for relative, _name, function in DISCOVERED:
        offender = _new_candidate_offender(
            relative, function, known, documented, _module_strings(_module(relative))
        )
        if offender:
            offenders.append(offender)
    assert not offenders, (
        "New one-time validator with a plain delete and no atomic claim:\n  "
        + "\n  ".join(sorted(offenders))
    )
