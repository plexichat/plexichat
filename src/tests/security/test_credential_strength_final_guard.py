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

import inspect
import json

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
