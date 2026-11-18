"""Regression: literal backslash in a URL authority must not bypass host gates.

``urlparse`` treats a literal ``\\`` as an ordinary character inside a URL
authority, while http(s) clients (requests/urllib3) and browsers follow the
WHATWG rule and read ``\\`` as ``/``. A URL such as::

    http://169.254.169.254\\@public.example/x

is therefore seen by ``urlparse(url).hostname`` as the public host
``public.example`` (the text to the RIGHT of the ``@``), but the downstream
client connects to ``169.254.169.254`` (to the LEFT of the backslash). That
parser differential turns every gate that derives its host from ``urlparse``
into an SSRF bypass.

Two stored-URL surfaces had their own host checks but not the backslash check
that ``outbound.parse_outbound_url`` and ``web_push._require_public_endpoint``
already apply:

* report evidence links (later fetched/rendered by moderation tooling);
* profile links (opened by a viewer's browser).

Both must refuse a literal backslash in the authority. Percent-encoded
``%5C`` and a backslash in the path are data, not authority, and stay allowed.
"""

import pytest

from src.api.routes.features.reports import _is_safe_url
from src.core.profiles.manager import _validate_profile_url
from src.core.security.errors import OutboundUrlRejected
from src.core.security.hardening import HardeningViolation

pytestmark = [pytest.mark.unit, pytest.mark.security]


BACKSLASH_AUTHORITY_URLS = [
    "http://169.254.169.254\\@public.example/latest/meta-data/",
    "https://127.0.0.1\\@public.example/x",
    "https://10.0.0.5\\@public.example/x",
    "https://metadata\\public.example/x",
    "https://8.8.8.8\\public.example/x",
]


@pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_URLS)
def test_report_evidence_gate_rejects_backslash_authority(url):
    # urlparse would derive the public-looking host from the right of the '@';
    # the gate must look at netloc and refuse before any host/IP decision.
    assert _is_safe_url(url) is False


@pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_URLS)
def test_profile_url_gate_rejects_backslash_authority(url):
    # Credential-bearing forms are already refused by require_http_url; the
    # plain forms reach the host gate, where the new netloc check must fire.
    with pytest.raises((OutboundUrlRejected, HardeningViolation)):
        _validate_profile_url(url, "banner_url")


def test_profile_host_gate_names_backslash_on_non_credential_form():
    # This URL passes require_http_url (well-formed https, no credentials), so
    # the refusal must come from the host gate's authority-backslash check.
    with pytest.raises(OutboundUrlRejected, match="backslash"):
        _validate_profile_url("https://8.8.8.8\\public.example/x", "banner_url")


@pytest.mark.parametrize(
    "url",
    [
        "https://8.8.8.8/x",
        "https://8.8.8.8/path?q=one%5Ctwo",
        "https://8.8.8.8/ep\\something",
    ],
)
def test_non_authority_backslashes_and_public_links_still_allowed(url):
    # No DNS involved: use public IP literals so the gate's resolver path is
    # deterministic. A percent-encoded backslash never changes host selection.
    assert _is_safe_url(url) is True
    assert _validate_profile_url(url, "banner_url") == url
