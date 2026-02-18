from src.api.schemas.relationships import RelationshipResponse
from src.api.schemas.common import SnowflakeID


def _relationship_to_response(rel) -> RelationshipResponse:
    status = getattr(rel, "status", None)
    if status is not None and hasattr(status, "value"):
        status = status.value

    return RelationshipResponse(
        user_id=SnowflakeID(getattr(rel, "user_id", 0) or getattr(rel, "target_id", 0)),
        status=status or "none",
        created_at=getattr(rel, "created_at", None),
    )
