"""Fail-closed authorization guard for artifact access (IDOR defence).

An endpoint that looks up an artifact by client supplied ID and then streams it
back is an IDOR waiting to happen: guessing somebody else's ID is enough unless
the object is both *bound to the requested ID* and *authorized for the calling
user*. The guard below makes those two checks explicit and unconditional.

It deliberately takes the authorization decision from a caller supplied
callback: the storage layer does not know the application's conversation /
server membership rules, but it can guarantee that the decision is always
asked for, with the right fields, and that a missing or ambiguous record fails
closed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

ArtifactAuthorizer = Callable[..., bool]


class ArtifactAccessDenied(PermissionError):
    """Raised when an artifact is not bound to the request or not permitted."""


def _positive_id(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ArtifactAccessDenied(f"Invalid {label}") from exc
    if parsed <= 0 or parsed > 2**63 - 1:
        raise ArtifactAccessDenied(f"Invalid {label}")
    return parsed


def _optional_id(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _positive_id(value, label)


def authorize_artifact_access(
    *,
    requested_id: Any,
    artifact: Any,
    user_id: Any,
    can_access: ArtifactAuthorizer,
    permission: str = "artifact.view",
) -> None:
    """Raise unless ``artifact`` is the requested object and ``user_id`` may see it.

    ``can_access`` is called with keyword arguments ``user_id``,
    ``conversation_id``, ``server_id``, ``author_id`` and ``permission``. It
    must return ``True``; any other value (including exceptions) denies access.
    """
    if artifact is None:
        raise ArtifactAccessDenied("Artifact not found")

    wanted = _positive_id(requested_id, "artifact id")
    actual_raw = getattr(artifact, "id", None)
    actual = _positive_id(actual_raw, "stored artifact id")

    # Binding the fetched object to the requested identifier stops an IDOR
    # where a repository helper returns a different record than was asked for.
    if actual != wanted:
        raise ArtifactAccessDenied("Artifact does not match the requested id")

    caller = _positive_id(user_id, "user id")
    kwargs = {
        "user_id": caller,
        "conversation_id": _optional_id(
            getattr(artifact, "conversation_id", None), "conversation id"
        ),
        "server_id": _optional_id(getattr(artifact, "server_id", None), "server id"),
        "author_id": _optional_id(getattr(artifact, "author_id", None), "author id"),
        "permission": permission,
    }
    try:
        allowed = can_access(**kwargs)
    except Exception as exc:  # an authorizer failure must never grant access
        raise ArtifactAccessDenied("Authorization check failed") from exc
    if allowed is not True:
        raise ArtifactAccessDenied("Access to artifact denied")
