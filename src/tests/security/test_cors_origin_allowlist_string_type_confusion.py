"""Regression: a scalar ``api.cors_origins`` must stay an exact allowlist.

Starlette's CORSMiddleware validates an origin with ``origin in allow_origins``.
When the config value is a plain string (a realistic YAML scalar form, already
accepted by the CSRF and error-response CORS helpers), that membership test is
a SUBSTRING match: configuring ``https://good.example`` also accepts
``https://good`` and reflects it with ``Access-Control-Allow-Credentials: true``.
``_build_api_config`` must normalise the value to a list of literal origins
before the middleware is built from it.
"""

from starlette.middleware.cors import CORSMiddleware


def _api_config(monkeypatch, api_conf):
    import utils.config as config
    from src.api import config as api_config

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: api_conf if key == "api" else default,
    )
    api_config.clear_api_config_cache()
    try:
        return api_config.get_api_config()
    finally:
        api_config.clear_api_config_cache()


def test_scalar_cors_origin_is_normalized_to_exact_list(monkeypatch):
    cfg = _api_config(monkeypatch, {"cors_origins": "https://good.example"})
    assert cfg.cors_origins == ["https://good.example"]


def test_scalar_cors_origin_cannot_reflect_substring_origin(monkeypatch):
    cfg = _api_config(
        monkeypatch,
        {
            "cors_origins": "https://good.example",
            "cors_allow_credentials": True,
        },
    )
    middleware = CORSMiddleware(
        app=None,
        allow_origins=cfg.cors_origins,
        allow_credentials=cfg.cors_allow_credentials,
        allow_methods=["GET", "POST"],
        allow_headers=[],
    )

    # Prefix / substring look-alikes must not be accepted as allowed origins.
    assert middleware.is_allowed_origin("https://good") is False
    assert middleware.is_allowed_origin("https://good.exa") is False
    assert middleware.is_allowed_origin("https://evil.example") is False

    # The explicitly configured origin must still work.
    assert middleware.is_allowed_origin("https://good.example") is True


def test_scalar_wildcard_still_fails_closed(monkeypatch):
    assert _api_config(monkeypatch, {"cors_origins": "*"}).cors_origins == []

    opted_in = _api_config(
        monkeypatch,
        {
            "cors_origins": "*",
            "allow_wildcard_cors": True,
            "cors_allow_credentials": True,
        },
    )
    assert opted_in.cors_origins == []
