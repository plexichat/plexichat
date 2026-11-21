"""Bot-profile URL fields must use the canonical HTTP-URL policy.

``UpdateBotProfileRequest`` validates ``avatar_url``/``banner_url``/the three
informational links with a hand-rolled ``urlparse`` scheme+netloc check. That
check accepts values the rest of the API rejects at the boundary:

* credential-bearing URLs (``https://user:pass@example.com``);
* a literal backslash in the authority, which ``urlparse`` reads as part of
  userinfo but browsers/curl normalise to ``/`` -- a link that validates as
  ``example.com`` can navigate visitors to ``evil.test``;
* embedded control characters and bidi overrides, which are carried verbatim
  into the public bot directory/profile response.

The core only revalidates the three informational links; ``avatar_url`` and
``banner_url`` are stored unchanged, so the route schema is the only gate for
those two. Pin all five fields to the shared policy instead.
"""

import importlib

import pytest
from pydantic import ValidationError


URL_FIELDS = (
    "avatar_url",
    "banner_url",
    "website_url",
    "support_url",
    "github_url",
)

# Each value passes the old ``scheme in (http, https) and netloc`` check.
UNSAFE_URLS = (
    "https://user:pass@example.com/x",
    "https://evil.test\\@example.com/x",
    "https://example.com/a\x00b",
    "https://example.com/a\nInjected: header",
    "https://example.com/\u202eexe.moc",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "//example.com/x",
)


def _request_model():
    return importlib.import_module("src.api.routes.bots").UpdateBotProfileRequest


@pytest.mark.parametrize("field", URL_FIELDS)
@pytest.mark.parametrize("value", UNSAFE_URLS)
def test_bot_profile_url_fields_reject_unsafe_values(field, value):
    with pytest.raises(ValidationError):
        _request_model()(**{field: value})


@pytest.mark.parametrize("field", URL_FIELDS)
def test_bot_profile_url_fields_normalise_valid_urls(field):
    model = _request_model()(**{field: "  https://example.com/path  "})
    assert getattr(model, field) == "https://example.com/path"


def test_bot_profile_url_fields_allow_omitted_and_reject_blank():
    model = _request_model()()
    assert all(getattr(model, field) is None for field in URL_FIELDS)
    with pytest.raises(ValidationError):
        _request_model()(avatar_url="   ")
