# Feature-route hardening

## W18 verified fixes

The feature API now applies consistent boundary validation and safe error handling:

- IDs are parsed as positive decimal values; booleans, signs, zero, negatives, and malformed values are rejected.
- Shared service adapters map validation and authorization failures to static client-safe messages while logging diagnostic details server-side.
- Enhanced-report evidence URLs reject credentials and private, loopback, link-local, multicast, reserved, and unspecified destinations.
- Onboarding preset failures expose a generic step-creation error rather than raw exception text.
- Voice metadata rejects unsafe storage schemes and private-network destinations while allowing validated local media paths and public HTTPS storage URLs.
- Audit-log and report projections support both mapping and tuple-backed database adapters.
- Feature-route failures use structured logging without leaking database or implementation details.

Regression coverage is in `src/tests/features/test_next_findings_security.py`. The focused feature and static-client suites pass with 41 tests.
