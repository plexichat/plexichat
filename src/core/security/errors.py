"""Security rejection taxonomy.

Every refusal the security layer makes raises one of these instead of a bare
``ValueError``. Two things follow from that:

* The HTTP layer maps the class to a status and a *static* message, so a reason
  like "resolved to 10.0.0.5" can never reach a client (that is a free port
  scan). The detail is logged server-side, keyed by ``code``.
* ``str(exc)`` stays safe to interpolate into an existing handler that already
  turns ``ValueError`` into a 400, which is how the pre-existing call sites
  (routes that wrap validation in ``except ValueError``) keep working
  unchanged.
"""

from __future__ import annotations

from typing import Optional


class SecurityRejection(ValueError):
    """Base class for a refusal made on security grounds.

    Subclasses set ``code`` (a stable machine-readable string) and
    ``public_message`` (what the client is allowed to see).
    """

    code: str = "security_rejected"
    public_message: str = "Request rejected on security grounds"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        field: Optional[str] = None,
    ) -> None:
        super().__init__(message or self.public_message)
        self.field = field

    @property
    def public_detail(self) -> dict[str, str]:
        """Client-safe detail for an API error body."""
        detail = {"code": self.code, "message": self.public_message}
        if self.field:
            detail["field"] = self.field
        return detail


class TextRejected(SecurityRejection):
    """A user-facing text field carried characters that are unsafe to echo."""

    code = "text_rejected"
    public_message = "Text must not contain HTML metacharacters or control characters"


class TextTooLong(SecurityRejection):
    """A text field exceeded the length its policy allows."""

    code = "text_too_long"
    public_message = "Text is too long"


class OutboundUrlRejected(SecurityRejection):
    """An attacker-supplied URL pointed at a scheme or address we must not use."""

    code = "outbound_url_rejected"
    public_message = "URL resolved to a forbidden address"


class UploadRejected(SecurityRejection):
    """An uploaded file failed content, type or size validation."""

    code = "upload_rejected"
    public_message = "Upload rejected"
