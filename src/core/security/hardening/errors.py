"""The exception every hardening helper raises.

Subclasses ``ValueError`` so existing ``except ValueError`` handlers keep
working (the API's rejection handler treats it like any other bad value).
"""


class HardeningViolation(ValueError):
    """A value violated a defense-in-depth invariant."""
