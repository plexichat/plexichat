"""Compression is actually wired into the real application stack.

The middleware's own unit tests drive a bare FastAPI app; these assert the
behavior through ``create_app()`` so a middleware-ordering change (compression
registered inside something that buffers or rewrites responses) fails here.
"""


def test_json_responses_are_gzipped_through_the_real_app(test_client):
    response = test_client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"].lower().find("accept-encoding") != -1
    # TestClient decodes transparently; the payload still has to be intact.
    assert response.json()["openapi"]


def test_clients_without_gzip_still_get_the_full_body(test_client):
    response = test_client.get("/openapi.json", headers={"Accept-Encoding": "identity"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"]
    assert response.json()["openapi"]


def test_range_requests_are_answered_without_encoding(test_client):
    response = test_client.get(
        "/openapi.json",
        headers={"Accept-Encoding": "gzip", "Range": "bytes=0-63"},
    )

    assert "content-encoding" not in response.headers
