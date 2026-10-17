"""HTTP-level checks that snowflake ids cross the wire as strings.

Complements ``test_openapi_snowflake_ids`` (which asserts the declared
OpenAPI schema) by exercising the real routes: ids must come back as
decimal strings, request bodies must accept a string id, and path params
must accept the decimal string instead of a JSON number.
"""

import uuid


def _unique_hash() -> str:
    # 32 hex chars, comfortably within the 16-128 char HashReportRequest range.
    return uuid.uuid4().hex


class TestResponseBodyStringIds:
    def test_session_list_ids_are_strings(self, test_client, auth_headers):
        r = test_client.get("/api/v1/auth/sessions", headers=auth_headers)
        assert r.status_code == 200, r.text
        sessions = r.json()
        assert sessions
        for session in sessions:
            assert isinstance(session["id"], str)
            assert session["id"].isdigit()
            device_id = session.get("device_id")
            if device_id is not None:
                assert isinstance(device_id, str)

    def test_current_user_id_is_a_string(self, test_client, auth_headers):
        r = test_client.get("/api/v1/users/@me", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert isinstance(r.json()["id"], str)


class TestPathParamStringIds:
    def test_presence_route_accepts_decimal_string_id(
        self, test_client, auth_headers, test_user_with_token
    ):
        uid = test_user_with_token["user"].id
        as_string = test_client.get(
            f"/api/v1/users/{uid}/presence", headers=auth_headers
        )
        as_int = test_client.get(
            f"/api/v1/users/{int(uid)}/presence", headers=auth_headers
        )
        # Accepting the string must be indistinguishable from the int form.
        assert as_string.status_code == as_int.status_code == 200, (
            as_string.text,
            as_int.text,
        )
        assert as_string.json() == as_int.json()

    def test_profile_route_accepts_decimal_string_id(
        self, test_client, auth_headers, test_user_with_token
    ):
        uid = test_user_with_token["user"].id
        r = test_client.get(
            f"/api/v1/features/users/{uid}/profile", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        profile = r.json()["profile"]
        # The profile payload's id is a string, matching the response contract.
        assert profile["user_id"] == str(uid)


class TestPushTokenIds:
    """The push-token CRUD endpoints return opaque dicts, so they are not
    visible to the OpenAPI id walk; pin the wire format over HTTP."""

    def test_registered_and_listed_token_ids_are_strings(
        self, test_client, auth_headers
    ):
        reg = test_client.post(
            "/api/v1/features/push/tokens",
            headers=auth_headers,
            json={"token": "test-fcm-token-abcdef123456", "platform": "android"},
        )
        assert reg.status_code == 200, reg.text
        record = reg.json()["token_record"]
        # id is a generated snowflake; user_id is the owner's snowflake.
        assert isinstance(record["id"], str)
        assert isinstance(record["user_id"], str)

        listed = test_client.get("/api/v1/features/push/tokens", headers=auth_headers)
        assert listed.status_code == 200, listed.text
        tokens = listed.json()["tokens"]
        assert tokens
        for token in tokens:
            assert isinstance(token["id"], str)
            assert isinstance(token["user_id"], str)


class TestRequestBodyStringIds:
    def test_hash_report_accepts_string_uploader_and_message_id(
        self, test_client, auth_headers, test_user_with_token
    ):
        uid = test_user_with_token["user"].id
        r = test_client.post(
            "/api/v1/media/report",
            headers=auth_headers,
            json={
                "hash_value": _unique_hash(),
                "reason": "string-id contract check",
                "uploader_id": str(uid),
                "message_id": str(uid),
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

    def test_hash_report_still_accepts_numeric_ids(
        self, test_client, auth_headers, test_user_with_token
    ):
        uid = int(test_user_with_token["user"].id)
        r = test_client.post(
            "/api/v1/media/report",
            headers=auth_headers,
            json={
                "hash_value": _unique_hash(),
                "reason": "numeric back-compat check",
                "uploader_id": uid,
                "message_id": uid,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True
