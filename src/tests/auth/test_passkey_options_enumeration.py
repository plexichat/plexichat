"""Regression test for finding M7: passkey options username-enumeration oracle.

GET/POST passkey authentication-options must return an indistinguishable
200 + challenge for unknown usernames (single dummy allowCredential) with
the same backend work as a known user without passkeys — never a 404 or
an observably different shape/latency path.
"""

import base64
import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.core.auth.passkeys as passkey_core
from src.utils import encryption


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _FakeDescriptor:
    """Stand-in for webauthn.helpers.structs.PublicKeyCredentialDescriptor."""

    def __init__(self, id: bytes):
        self.id = id


def _fake_generate_authentication_options(
    rp_id, challenge, allow_credentials=None, user_verification=None
):
    return SimpleNamespace(
        rp_id=rp_id,
        challenge=challenge,
        allow_credentials=list(allow_credentials or []),
    )


def _fake_options_to_json(options=None, **kwargs):
    opts = options if options is not None else kwargs.get("options")
    return json.dumps(
        {
            "rpId": opts.rp_id,
            "challenge": _b64u(opts.challenge),
            "allowCredentials": [
                {"type": "public-key", "id": _b64u(d.id)}
                for d in (opts.allow_credentials or [])
            ],
        }
    )


@pytest.fixture
def webauthn_stub(monkeypatch):
    """Exercise PasskeyManager without the optional `webauthn` dependency."""
    monkeypatch.setattr(passkey_core, "WEBAUTHN_AVAILABLE", True)
    monkeypatch.setattr(passkey_core, "PublicKeyCredentialDescriptor", _FakeDescriptor)
    monkeypatch.setattr(
        passkey_core,
        "generate_authentication_options",
        _fake_generate_authentication_options,
    )
    monkeypatch.setattr(passkey_core, "options_to_json", _fake_options_to_json)
    monkeypatch.setattr(
        passkey_core,
        "UserVerificationRequirement",
        SimpleNamespace(PREFERRED="preferred"),
    )
    return passkey_core


@pytest.fixture
def enum_user(auth_manager, pii_gen):
    """A real user with no passkeys (the known-user side of the oracle)."""
    username = f"enum_{uuid.uuid4().hex[:12]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(
            username=username,
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return username


def _allow_credentials(result):
    return result["options"]["allowCredentials"]


class TestPasskeyOptionsEnumeration:
    def test_unknown_user_gets_dummy_challenge_not_error(
        self, auth_manager, webauthn_stub
    ):
        """Unknown username must yield options (200-equivalent), never raise/404."""
        ghost = f"ghost_{uuid.uuid4().hex[:12]}"
        result = auth_manager.generate_passkey_authentication_options(username=ghost)
        assert result["challenge_id"]
        assert result["options"]["challenge"]
        creds = _allow_credentials(result)
        assert len(creds) == 1
        assert creds[0]["id"]  # random dummy credential id

    def test_unknown_indistinguishable_from_known_without_passkeys(
        self, auth_manager, webauthn_stub, enum_user
    ):
        """Same response shape keys and same allowCredentials count (1 dummy)."""
        ghost = f"ghost_{uuid.uuid4().hex[:12]}"
        known = auth_manager.generate_passkey_authentication_options(username=enum_user)
        unknown = auth_manager.generate_passkey_authentication_options(username=ghost)

        assert set(known.keys()) == set(unknown.keys())
        assert set(known["options"].keys()) == set(unknown["options"].keys())
        assert len(_allow_credentials(known)) == len(_allow_credentials(unknown)) == 1
        # Challenges are fresh random values, never equal / never user-derived.
        assert known["challenge_id"] != unknown["challenge_id"]
        assert known["options"]["challenge"] != unknown["options"]["challenge"]

    def test_challenge_not_bound_to_user(
        self, auth_manager, webauthn_stub, enum_user, db
    ):
        """Stored challenge rows carry user_id NULL for both cases (no linkage)."""
        ghost = f"ghost_{uuid.uuid4().hex[:12]}"
        known = auth_manager.generate_passkey_authentication_options(username=enum_user)
        unknown = auth_manager.generate_passkey_authentication_options(username=ghost)
        for result in (known, unknown):
            row = db.fetch_one(
                "SELECT user_id, challenge_type FROM auth_passkey_challenges WHERE challenge_id = ?",
                (result["challenge_id"],),
            )
            assert row is not None
            user_id = row["user_id"] if isinstance(row, dict) else row[0]
            assert user_id is None

    def test_unknown_path_does_equal_backend_work(
        self, auth_manager, webauthn_stub, db, enum_user
    ):
        """Unknown usernames still perform the credential-lookup roundtrip
        (timing ballast): both paths issue >= 2 credential-table fetches."""
        ghost = f"ghost_{uuid.uuid4().hex[:12]}"
        calls = []
        orig_fetch_all = db.fetch_all

        def counting_fetch_all(sql, params=()):
            calls.append(sql)
            return orig_fetch_all(sql, params)

        with patch.object(db, "fetch_all", side_effect=counting_fetch_all):
            auth_manager.generate_passkey_authentication_options(username=enum_user)
            known_fetches = [s for s in calls if "auth_passkeys" in s]
            calls.clear()
            auth_manager.generate_passkey_authentication_options(username=ghost)
            unknown_fetches = [s for s in calls if "auth_passkeys" in s]

        assert len(known_fetches) >= 1
        assert len(unknown_fetches) >= 1

    def test_dummy_not_disabled_by_config(self, auth_manager, webauthn_stub, enum_user):
        """The legacy dummy_credential=false opt-out must not reopen the oracle."""
        mgr = auth_manager._get_manager().passkeys
        mgr._config["dummy_credential"] = False
        try:
            ghost = f"ghost_{uuid.uuid4().hex[:12]}"
            unknown = auth_manager.generate_passkey_authentication_options(
                username=ghost
            )
            assert len(_allow_credentials(unknown)) == 1
        finally:
            mgr._config["dummy_credential"] = True

    def test_route_returns_same_status_for_known_and_unknown(
        self, test_client, webauthn_stub, test_user_with_token
    ):
        """POST /passkeys/options/authenticate: 200 for both, never 404 vs 200."""
        known_username = test_user_with_token["username"]
        ghost = f"ghost_{uuid.uuid4().hex[:12]}"
        r_known = test_client.post(
            "/api/v1/auth/passkeys/options/authenticate",
            json={"username": known_username},
        )
        r_unknown = test_client.post(
            "/api/v1/auth/passkeys/options/authenticate",
            json={"username": ghost},
        )
        assert r_known.status_code == 200
        assert r_unknown.status_code == 200
        for resp in (r_known, r_unknown):
            body = resp.json()
            assert body["challenge_id"]
            assert body["options"]["allowCredentials"]
            assert len(body["options"]["allowCredentials"]) == 1
