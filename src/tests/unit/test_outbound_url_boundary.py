"""Boundary test: SSRF gating happens in one place.

Three call sites had grown their own wrapper around ``URLValidator`` (profile
social links, the server icon URL, the media attachment URL), each with its own
scheme check and log message. They were consolidated into
``src/core/security/outbound.py``. This test fails if a route or schema starts
calling the validator directly again, which is how the duplication appeared the
first time.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

#: Files that must delegate rather than re-implement.
CONSOLIDATED = (
    "src/core/profiles/manager.py",
    "src/api/schemas/servers.py",
    "src/api/schemas/media.py",
)

#: The gate itself is the only place allowed to touch the IP validator.
GATE = "src/core/security/outbound.py"


def test_consolidated_sites_use_the_shared_gate():
    for relative in CONSOLIDATED:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "resolve_outbound_url" in source, (
            f"{relative} no longer uses the shared outbound gate"
        )
        assert "URLValidator" not in source, (
            f"{relative} regrew a local SSRF wrapper; call {GATE} instead"
        )
        assert "validate_url_for_request" not in source, (
            f"{relative} calls the IP validator directly; call resolve_outbound_url"
        )


def test_the_gate_is_the_only_ip_validator_caller_in_the_api_layer():
    offenders = []
    for path in sorted((ROOT / "src/api").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "validate_url_for_request" in text or "URLValidator" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        "The API layer must gate outbound URLs through "
        f"{GATE}; direct validator use found in: {offenders}"
    )


def test_media_security_settings_are_typed():
    # The dict-of-.get() access is what let a misspelled key disable a check.
    source = (ROOT / "src/core/media/security/validation.py").read_text(
        encoding="utf-8"
    )
    assert "FileSecuritySettings" in source
    assert 'self._config["' not in source, (
        "FileValidator reads security settings as a dict again; "
        "use FileSecuritySettings attributes"
    )
