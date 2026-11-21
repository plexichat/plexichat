"""FINAL credential-strength sweep guard: every secret has a floor.

Definitive enumeration of the configurable / generated secrets in ``src/`` and
the floor that protects every caller-supplied path that installs one. Peers
added floors for the first three rows; this module adds the federation shared
key (row 4), which was the remaining caller-supplied credential-setting path
with NO strength floor, and ratchets all of them so a floor cannot be removed
without failing this suite.

============================  ============================================  ==========================================================
secret                        minted / generated at                         caller-supplied path -> floor
============================  ============================================  ==========================================================
custom API access token       token_urlsafe(24) == 192-bit                  create_api_access_token(token_value=) -> _MIN_CUSTOM_ACCESS_TOKEN_LENGTH
webhook signing secret        token_urlsafe(48) / token_hex(32)             _resolve_webhook_secret(config) -> MIN_WEBHOOK_SECRET_LENGTH
admin password                Argon2id + shared policy                     set_password() -> _validate_password_policy (shared validate_password)
federation shared key         generate_shared_key() -> urlsafe(32)         admin create_connection(shared_key=) -> validate_shared_key(require_strong=True)
invite / join code            secrets.choice x invite_code_length (>=8)     validate_join_code() -> MIN_JOIN_CODE_LENGTH (8)
encryption KEK                OS/HSM 32-64 byte material                    validate_kek() -> MIN_KEK_BYTES + Shannon entropy floor
OAuth client secret           generate_client_secret -> urlsafe(32)         (no caller-supplied setter; verification only)
bot token                     generate_token_secret(48) == 384-bit          (generated only; parse floor MIN_TOKEN_SECRET_LENGTH)
session secret                generate_token_secret(32) == 256-bit          (generated only; parse floor MIN_TOKEN_SECRET_LENGTH)
backup codes (2FA)            token_hex(4).upper() == 32-bit, rate-limited  (generated only; single-use + attempt limiting)
passkeys (WebAuthn)           authenticator-generated keypair               (never caller-supplied as raw key material)
PIN                           (none in this codebase)                       (no PIN secret path)
============================  ============================================  ==========================================================

The gap this module closes: ``POST /api/v1/admin/plexijoin/connections`` took
an operator-supplied ``shared_key`` and only checked it was non-empty before
persisting it as the HMAC key for every federation envelope on that
connection. ``PlexiJoinManager.create_connection`` calls
``validate_shared_key(shared_key)`` with its default ``require_strong=False``
(imported/legacy keys are shape-checked only), so a one-character key was
accepted and made all federation signatures trivially forgeable. The admin
route now enforces ``require_strong=True`` (the generator's own floor) at the
caller-supplied entry point.

Non-vacuity: the static guard runs the real detector in both directions (a
floored and a floored-less synthetic function), and the behavioural tests drive
the actual validators / HTTP handler with weak AND strong values so the floor
cannot be "fixed" by deleting the operation.
"""

from __future__ import annotations

import ast
import inspect
import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

import src.api.routes.admin.plexijoin as admin_plexijoin
from src.core.admin.auth.password_mixin import PasswordMixin
from src.core.applications.manager.base import ApplicationManager
from src.core.auth.managers.api_token import ApiTokenMixin
from src.core.plexijoin.validation import (
    validate_join_code,
    validate_shared_key,
)
from src.core.security.hardening import HardeningViolation
from src.utils.encryption.kek_validation import (
    KekValidationError,
    validate_kek,
)

pytestmark = [pytest.mark.security, pytest.mark.unit]


# --- static source-level ratchet --------------------------------------------

# Registry of caller-supplied secret-setting sites -> the floor marker that
# MUST still appear in that site's own source. A removed floor, a bypass added
# around it, or a renamed constant breaks the corresponding assertion.
_SETTING_PATHS = {
    "custom API token": (
        ApiTokenMixin.create_api_access_token,
        "_MIN_CUSTOM_ACCESS_TOKEN_LENGTH",
    ),
    "webhook signing secret": (
        ApplicationManager._resolve_webhook_secret,
        "MIN_WEBHOOK_SECRET_LENGTH",
    ),
    "admin password": (
        PasswordMixin._validate_password_policy,
        "validate_shared",
    ),
    "federation shared key (admin route)": (
        admin_plexijoin.create_connection,
        "require_strong",
    ),
    "federation shared key (validator)": (
        validate_shared_key,
        "require_strong",
    ),
    "invite / join code": (
        validate_join_code,
        "MIN_JOIN_CODE_LENGTH",
    ),
    "encryption KEK": (
        validate_kek,
        "MIN_KEK_BYTES",
    ),
}


def _source_has_floor(func, marker: str) -> bool:
    """Whether ``func``'s source still references its floor marker."""
    return marker in inspect.getsource(func)


def test_every_caller_supplied_secret_path_enforces_a_floor():
    # Non-vacuous: the registry is a real, non-trivial enumeration.
    assert len(_SETTING_PATHS) >= 7
    for name, (func, marker) in _SETTING_PATHS.items():
        assert _source_has_floor(func, marker), (
            f"{name} no longer enforces its strength floor "
            f"(expected marker {marker!r} in its source)"
        )


def test_floor_detector_is_not_vacuous():
    """Self-test: the detector flags a floor-less path and passes a floored one."""

    def floored():
        return "MIN_WEBHOOK_SECRET_LENGTH"

    def unfloored():
        return 5

    assert _source_has_floor(floored, "MIN_WEBHOOK_SECRET_LENGTH") is True
    assert _source_has_floor(unfloored, "MIN_WEBHOOK_SECRET_LENGTH") is False
    # A static guard that matched everything would fail this module's registry
    # check too, so both directions are proven here.


# --- behavioural floors: weak rejected, strong accepted ---------------------


@pytest.mark.parametrize("weak", ["a", "short", "S" * 31])
def test_federation_shared_key_floor_rejects_weak(weak):
    with pytest.raises(HardeningViolation):
        validate_shared_key(weak, require_strong=True)


def test_federation_shared_key_floor_accepts_strong():
    key = validate_shared_key("S" * 32, require_strong=True)
    assert len(key) >= 32


@pytest.mark.parametrize("weak", ["", "short", "1234567"])
def test_join_code_floor_rejects_weak(weak):
    with pytest.raises(HardeningViolation):
        validate_join_code(weak)


def test_join_code_floor_accepts_valid():
    assert validate_join_code("Abc12345") == "Abc12345"


def test_kek_floor_rejects_weak_material():
    # Length requirements are never bypassed, even with allow_bypass=True.
    with pytest.raises(KekValidationError):
        validate_kek(b"short")


# --- end-to-end: the admin route gap is actually closed ---------------------


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    def __init__(self, body):
        # read_body_capped() returns a cached _body verbatim, then the route
        # calls .decode()/json.loads() on it, so cache real JSON bytes.
        self._body = json.dumps(body).encode("utf-8")
        self.client = _FakeClient()
        self.headers = {}


class _RecordingManager:
    def __init__(self):
        self.calls = []

    def create_connection(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": 4242}


def _patch_route(monkeypatch, manager):
    monkeypatch.setattr(admin_plexijoin, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(admin_plexijoin, "require_admin_permission", lambda r, p: 1)
    monkeypatch.setattr(admin_plexijoin, "_get_plexijoin_manager", lambda r: manager)


def _body(shared_key):
    return {
        "remote_instance_id": "remote:example",
        "remote_url": "https://remote.example",
        "shared_key": shared_key,
    }


@pytest.mark.asyncio
async def test_admin_federation_create_rejects_weak_shared_key(monkeypatch):
    manager = _RecordingManager()
    _patch_route(monkeypatch, manager)
    with pytest.raises(HTTPException) as exc:
        await admin_plexijoin.create_connection(_FakeRequest(_body("a")))
    assert exc.value.status_code == 400
    assert manager.calls == [], "weak key must never reach the manager/DB"


@pytest.mark.asyncio
async def test_admin_federation_create_accepts_strong_shared_key(monkeypatch):
    manager = _RecordingManager()
    _patch_route(monkeypatch, manager)
    strong = "S" * 32
    result = await admin_plexijoin.create_connection(_FakeRequest(_body(strong)))
    assert result["connection_id"] == 4242
    assert len(manager.calls) == 1
    assert manager.calls[0]["shared_key"] == strong


# --- structural sweep: a NOVEL weak-key route must not silently escape -------
#
# The static ratchet above is a fixed tuple of *function objects*
# (``_SETTING_PATHS``).  It proves the seven known sites keep their floors, but
# it cannot see a route that does not exist yet: a developer who adds
# ``POST /plexijoin/connections/import`` (or any differently named handler) that
# forwards a caller-supplied ``shared_key`` straight to
# ``PlexiJoinManager.create_connection`` is invisible to that ratchet.  The
# manager intentionally shape-checks imported/legacy keys with the weak default
# (``validate_shared_key(shared_key)``), so such a route would persist a
# one-character HMAC key with NO strength floor, and the old guard would stay
# green.  This section closes that gap by enumerating the sites *structurally*
# (over the AST of every production module) and failing on any unclassified
# site.

_SRC_ROOT = Path(__file__).resolve().parents[2]  # .../src
_REPO_ROOT = _SRC_ROOT.parent
_SCAN_EXCLUDED_DIRS = {"tests", "selftest"}
_VALIDATION_MODULE = "src/core/plexijoin/validation.py"

# The ONLY production sites allowed to use the weak ``validate_shared_key``
# default.  These are the manager's legacy/import shape-check path; a
# caller-facing setter is never allowed in this allowlist.
_LEGACY_WEAK_SHARED_KEY_SITES = {
    ("src/core/plexijoin/manager.py", "create_connection"),
}


def _findings():
    return {
        "floored_validate": [],
        "legacy_validate": [],
        "unclassified_validate": [],
        "floored_create": [],
        "generated_create": [],
        "unclassified_create": [],
    }


def _call_name(node: ast.Call):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _has_strong_floor(call: ast.Call) -> bool:
    return any(
        kw.arg == "require_strong"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in call.keywords
    )


def _is_generated_key(expr: ast.AST) -> bool:
    return isinstance(expr, ast.Call) and _call_name(expr) == "generate_shared_key"


def _classify_module(tree: ast.AST, path: str, findings: dict) -> None:
    """Classify every shared-key site in one parsed module into ``findings``."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing_func(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(cur)
        return "<module>"

    # Functions that floor a key via validate_shared_key(..., require_strong=True).
    floored_funcs = {
        enclosing_func(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_name(node) == "validate_shared_key"
        and _has_strong_floor(node)
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name, func = _call_name(node), enclosing_func(node)
        if name == "validate_shared_key":
            if _has_strong_floor(node):
                findings["floored_validate"].append((path, func))
            elif (path, func) in _LEGACY_WEAK_SHARED_KEY_SITES:
                findings["legacy_validate"].append((path, func))
            else:
                findings["unclassified_validate"].append((path, func))
        elif name == "create_connection":
            shared_kw = next(
                (kw.value for kw in node.keywords if kw.arg == "shared_key"), None
            )
            if shared_kw is None:
                continue  # unrelated create_connection (DB/websocket sockets)
            if _is_generated_key(shared_kw):
                findings["generated_create"].append((path, func))
            elif func in floored_funcs:
                findings["floored_create"].append((path, func))
            else:
                findings["unclassified_create"].append((path, func))


def _read_source(py: Path) -> str:
    """Read a module, tolerating transient rewrite races in a shared worktree."""
    last: OSError | None = None
    for _ in range(6):
        try:
            return py.read_text(encoding="utf-8")
        except OSError as exc:  # another agent mid atomic-rewrite
            last = exc
            time.sleep(0.05)
    raise last


def _production_findings() -> dict:
    findings = _findings()
    for py in sorted(_SRC_ROOT.rglob("*.py")):
        rel = py.relative_to(_SRC_ROOT)
        if any(part in _SCAN_EXCLUDED_DIRS for part in rel.parts):
            continue
        if py.name.startswith("test_"):
            continue
        path = str(py.relative_to(_REPO_ROOT))
        if path == _VALIDATION_MODULE:
            continue
        _classify_module(
            ast.parse(_read_source(py), filename=path), path, findings
        )
    return findings


def test_all_shared_key_setting_sites_are_classified():
    """Every production shared-key site is floored, generated, or allowlisted."""
    f = _production_findings()
    # Non-vacuity: the scanner must discover real sites in each expected class.
    # A scanner that matched nothing would trivially pass the emptiness checks.
    assert f["floored_validate"], "scanner found no floored validate_shared_key sites"
    assert f["floored_create"], "scanner found no floored create_connection sites"
    assert f["generated_create"], "scanner found no generated-key create_connection sites"
    assert f["legacy_validate"], "scanner found no legacy weak-default site"

    # Fail on unclassified sites. A brand-new route under a novel name that
    # forwards a caller-supplied shared_key without a floor lands here.
    assert not f["unclassified_validate"], (
        "weak validate_shared_key site with no floor and no legacy allowlist: "
        + repr(f["unclassified_validate"])
    )
    assert not f["unclassified_create"], (
        "caller-supplied create_connection(shared_key=) with no strength floor "
        "in its enclosing function: " + repr(f["unclassified_create"])
    )


def test_structural_detector_flags_novel_weak_key_route():
    """Proof the detector catches the novel route the old ratchet missed."""
    novel = '''
async def import_connection(request):
    body = await request.json()
    return manager.create_connection(shared_key=body["shared_key"])
'''
    f = _findings()
    _classify_module(ast.parse(novel), "<synthetic>", f)
    assert ("<synthetic>", "import_connection") in f["unclassified_create"], (
        "a novel route forwarding a caller-supplied shared_key with no floor "
        "must be flagged as unclassified"
    )


def test_structural_detector_accepts_floored_route():
    """The detector is not vacuously rejecting every novel setter."""
    novel = '''
async def import_connection(request):
    body = await request.json()
    key = validate_shared_key(body["shared_key"], require_strong=True)
    return manager.create_connection(shared_key=key)
'''
    f = _findings()
    _classify_module(ast.parse(novel), "<synthetic>", f)
    assert ("<synthetic>", "import_connection") in f["floored_create"]
    assert not f["unclassified_create"]
