"""Protocol class for query builder mixins."""

from typing import Any, List, Optional, Type, cast

from pydantic import BaseModel


class QueryBuilderProtocol:
    """Protocol for query builder components shared across mixin boundaries."""

    connection: Any
    db_type: str
    _validation_model: Optional[Type[BaseModel]] = None
    _sql: str = ""
    _params: List[Any]

    def build(self) -> tuple[str, list]:
        return cast(QueryBuilderProtocol, super()).build()

    def execute(self) -> Any:
        return cast(QueryBuilderProtocol, super()).execute()

    def _validate_identifier(
        self, identifier: str, identifier_type: str = "column"
    ) -> str:
        return cast(QueryBuilderProtocol, super())._validate_identifier(
            identifier, identifier_type
        )

    def _validate_identifiers_list(
        self, identifiers: List[str], identifier_type: str = "column"
    ) -> List[str]:
        return cast(QueryBuilderProtocol, super())._validate_identifiers_list(
            identifiers, identifier_type
        )
