"""Avatar upload read failures must not disclose server filesystem paths.

The upload handler wraps ``read_upload_capped`` in a broad ``except Exception``
and used to build the client-visible 400 message with ``str(e)``.  An
``OSError``/``ClientDisconnect`` there carries an absolute path (spooled
tempfile, missing upload directory, ...) which was echoed straight back to the
caller -- a CWE-209 path disclosure.  The detail belongs in the server log
only; clients get a stable, actionable message and the original 400 code.
"""

from io import BytesIO

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
# Distinctive path with no log-sanitizer keyword trigger (no "password",
# "token", "key", "secret", ...), so any appearance in the body is a raw leak.
SECRET_PATH = "/srv/plexichat/internal/store/accounts.sqlite3"


async def _raise_os_error(file, limit):
    raise OSError(f"[Errno 13] Permission denied: '{SECRET_PATH}'")


def test_upload_avatar_read_error_does_not_leak_path(
    test_client, test_user_with_token, monkeypatch
):
    token = test_user_with_token["token"]
    monkeypatch.setattr(
        "src.api.routes.avatars.routes.read_upload_capped", _raise_os_error
    )

    resp = test_client.post(
        "/api/v1/avatars/users/@me",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("avatar.png", BytesIO(PNG), "image/png")},
    )

    assert resp.status_code == 400, resp.text
    # Fail closed: no absolute path, no raw exception text in the body.
    assert SECRET_PATH not in resp.text
    assert "Permission denied" not in resp.text
    # Still an actionable, stable client error with the legitimate code kept.
    body = resp.json()["error"]
    assert body["code"] == 400
    assert body["message"] == "Failed to read file"
