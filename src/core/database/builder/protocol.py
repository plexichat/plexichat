"""Protocol class for query builder mixins."""

from typing import Any, List, Optional, Type

from pydantic import BaseModel


class QueryBuilderProtocol:
    """Protocol for query builder components shared across mixin boundaries."""

    connection: Any
    db_type: str
    _validation_model: Optional[Type[BaseModel]] = None
    _sql: str = ""
    _params: List[Any]

    def build(self) -> tuple[str, list]:
        return super().build()  # type: ignore[misc]

    def execute(self) -> Any:
        return super().execute()  # type: ignore[misc]

    def _validate_identifier(
        self, identifier: str, identifier_type: str = "column"
    ) -> str:
        return super()._validate_identifier(identifier, identifier_type)  # type: ignore[misc]

    def _validate_identifiers_list(
        self, identifiers: List[str], identifier_type: str = "column"
    ) -> List[str]:
        return super()._validate_identifiers_list(identifiers, identifier_type)  # type: ignore[misc]
