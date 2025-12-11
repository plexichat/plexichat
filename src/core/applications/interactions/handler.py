"""
Interaction handler - Process and respond to interactions.
"""

import time
import json
from typing import Optional, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from ..models import (
    Interaction, InteractionData, InteractionResponse,
    InteractionType, InteractionResponseType, ComponentType, CommandType,
)
from ..exceptions import (
    InteractionNotFoundError,
    InteractionExpiredError,
    InteractionAlreadyRespondedError,
    InteractionValidationError,
)
from ..oauth.tokens import generate_interaction_token, verify_token_hash, parse_oauth_token
from .components import validate_components


INTERACTION_TOKEN_EXPIRY = 900


class InteractionHandler:
    """Handles interaction processing and responses."""

    def __init__(self, db, config: Dict[str, Any], events_module=None):
        """
        Initialize interaction handler.
        
        Args:
            db: Database instance
            config: Interaction configuration
            events_module: Events module for dispatching
        """
        self._db = db
        self._config = config
        self._events = events_module

    def _current_time(self) -> int:
        """Get current Unix timestamp."""
        return int(time.time())

    def create_interaction(
        self,
        application_id: int,
        interaction_type: InteractionType,
        user_id: int,
        data: Optional[Dict[str, Any]] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        message_id: Optional[int] = None,
        locale: Optional[str] = None,
        server_locale: Optional[str] = None,
    ) -> Interaction:
        """
        Create a new interaction.
        
        Args:
            application_id: Application ID
            interaction_type: Type of interaction
            user_id: User who triggered interaction
            data: Interaction data
            server_id: Server ID
            channel_id: Channel ID
            message_id: Message ID (for component interactions)
            locale: User locale
            server_locale: Server locale
            
        Returns:
            Interaction with token
        """
        interaction_id = generate_snowflake_id()
        now = self._current_time()

        full_token, token_hash = generate_interaction_token(interaction_id)

        data_json = json.dumps(data) if data else None

        self._db.execute(
            """INSERT INTO app_interactions
               (id, application_id, interaction_type, data, server_id, channel_id,
                user_id, token_hash, version, message_id, locale, server_locale, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (interaction_id, application_id, interaction_type.value, data_json,
             server_id, channel_id, user_id, token_hash, 1, message_id,
             locale, server_locale, now)
        )

        interaction_data = None
        if data:
            interaction_data = self._parse_interaction_data(data)

        logger.debug(f"Interaction created: {interaction_id} for app {application_id}")

        return Interaction(
            id=interaction_id,
            application_id=application_id,
            interaction_type=interaction_type,
            data=interaction_data,
            server_id=server_id,
            channel_id=channel_id,
            user_id=user_id,
            token=full_token,
            version=1,
            message_id=message_id,
            locale=locale,
            server_locale=server_locale,
            created_at=now,
            token_hash=token_hash,
        )

    def get_interaction(self, interaction_id: int) -> Optional[Interaction]:
        """
        Get an interaction by ID.
        
        Args:
            interaction_id: Interaction ID
            
        Returns:
            Interaction or None
        """
        row = self._db.fetch_one(
            """SELECT id, application_id, interaction_type, data, server_id,
                      channel_id, user_id, token_hash, version, message_id,
                      locale, server_locale, created_at, responded
               FROM app_interactions WHERE id = ?""",
            (interaction_id,)
        )

        if not row:
            return None

        return self._row_to_interaction(row)

    def verify_interaction_token(self, token: str) -> Interaction:
        """
        Verify an interaction token and return the interaction.
        
        Args:
            token: Interaction token
            
        Returns:
            Interaction
            
        Raises:
            InteractionNotFoundError: Interaction not found
            InteractionExpiredError: Token expired
        """
        parsed = parse_oauth_token(token)
        if not parsed or parsed["token_type"] != "int":
            raise InteractionNotFoundError("Invalid interaction token")

        interaction = self.get_interaction(parsed["id"])
        if not interaction:
            raise InteractionNotFoundError("Interaction not found")

        if not interaction.token_hash or not verify_token_hash(parsed["secret"], interaction.token_hash):
            raise InteractionNotFoundError("Invalid interaction token")

        expiry = self._config.get("interaction_timeout", INTERACTION_TOKEN_EXPIRY)
        if self._current_time() > interaction.created_at + expiry:
            raise InteractionExpiredError("Interaction token has expired")

        return interaction

    def respond(
        self,
        interaction_token: str,
        response: InteractionResponse,
    ) -> bool:
        """
        Respond to an interaction.
        
        Args:
            interaction_token: Interaction token
            response: Response to send
            
        Returns:
            True if response sent
            
        Raises:
            InteractionNotFoundError: Interaction not found
            InteractionExpiredError: Token expired
            InteractionAlreadyRespondedError: Already responded
            InteractionValidationError: Invalid response
        """
        interaction = self.verify_interaction_token(interaction_token)

        if interaction.responded:
            raise InteractionAlreadyRespondedError("Interaction has already been responded to")

        issues = self._validate_response(interaction, response)
        if issues:
            raise InteractionValidationError("Invalid response", issues)

        self._db.execute(
            "UPDATE app_interactions SET responded = 1 WHERE id = ?",
            (interaction.id,)
        )

        logger.debug(f"Interaction {interaction.id} responded with type {response.response_type}")

        return True

    def create_followup(
        self,
        interaction_token: str,
        content: Optional[str] = None,
        embeds: Optional[list] = None,
        components: Optional[list] = None,
        ephemeral: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a followup message for an interaction.
        
        Args:
            interaction_token: Interaction token
            content: Message content
            embeds: Message embeds
            components: Message components
            ephemeral: Whether message is ephemeral
            
        Returns:
            Followup message data
        """
        interaction = self.verify_interaction_token(interaction_token)

        message_id = generate_snowflake_id()

        return {
            "id": str(message_id),
            "interaction_id": str(interaction.id),
            "content": content,
            "embeds": embeds or [],
            "components": components or [],
            "flags": 64 if ephemeral else 0,
        }

    def edit_original(
        self,
        interaction_token: str,
        content: Optional[str] = None,
        embeds: Optional[list] = None,
        components: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Edit the original interaction response.
        
        Args:
            interaction_token: Interaction token
            content: New content
            embeds: New embeds
            components: New components
            
        Returns:
            Updated message data
        """
        interaction = self.verify_interaction_token(interaction_token)

        return {
            "interaction_id": str(interaction.id),
            "content": content,
            "embeds": embeds or [],
            "components": components or [],
        }

    def delete_original(self, interaction_token: str) -> bool:
        """
        Delete the original interaction response.
        
        Args:
            interaction_token: Interaction token
            
        Returns:
            True if deleted
        """
        self.verify_interaction_token(interaction_token)
        return True

    def dispatch_interaction(self, interaction: Interaction) -> None:
        """
        Dispatch an interaction event to the gateway.
        
        Args:
            interaction: Interaction to dispatch
        """
        if not self._events:
            return

        event_data = {
            "id": str(interaction.id),
            "application_id": str(interaction.application_id),
            "type": interaction.interaction_type.value,
            "token": interaction.token,
            "version": interaction.version,
        }

        if interaction.data:
            event_data["data"] = self._interaction_data_to_dict(interaction.data)

        if interaction.server_id:
            event_data["guild_id"] = str(interaction.server_id)

        if interaction.channel_id:
            event_data["channel_id"] = str(interaction.channel_id)

        event_data["user"] = {"id": str(interaction.user_id)}

        if interaction.message_id:
            event_data["message"] = {"id": str(interaction.message_id)}

        if interaction.locale:
            event_data["locale"] = interaction.locale

        if interaction.server_locale:
            event_data["guild_locale"] = interaction.server_locale

        logger.debug(f"Dispatching INTERACTION_CREATE for {interaction.id}")

    def _validate_response(
        self,
        interaction: Interaction,
        response: InteractionResponse,
    ) -> list:
        """Validate a response for an interaction."""
        issues = []

        if interaction.interaction_type == InteractionType.PING:
            if response.response_type != InteractionResponseType.PONG:
                issues.append("Ping interactions must respond with PONG")

        elif interaction.interaction_type == InteractionType.APPLICATION_COMMAND_AUTOCOMPLETE:
            if response.response_type != InteractionResponseType.APPLICATION_COMMAND_AUTOCOMPLETE_RESULT:
                issues.append("Autocomplete interactions must respond with autocomplete results")
            elif not response.choices:
                pass
            elif len(response.choices) > 25:
                issues.append("Autocomplete response exceeds 25 choices")

        elif interaction.interaction_type == InteractionType.MODAL_SUBMIT:
            if response.response_type == InteractionResponseType.MODAL:
                issues.append("Cannot respond to modal submit with another modal")

        if response.components:
            if response.response_type == InteractionResponseType.MODAL:
                valid, comp_issues = validate_components(response.components, "modal")
            else:
                valid, comp_issues = validate_components(response.components, "message")
            issues.extend(comp_issues)

        return issues

    def _parse_interaction_data(self, data: Dict[str, Any]) -> InteractionData:
        """Parse interaction data from dict."""
        command_type = data.get("type")
        if command_type is not None:
            command_type = CommandType(command_type) if isinstance(command_type, int) else command_type

        component_type = data.get("component_type")
        if component_type is not None:
            component_type = ComponentType(component_type) if isinstance(component_type, int) else component_type

        return InteractionData(
            id=data.get("id", 0),
            name=data.get("name", ""),
            command_type=command_type,
            resolved=data.get("resolved"),
            options=data.get("options"),
            custom_id=data.get("custom_id"),
            component_type=component_type,
            values=data.get("values"),
            target_id=data.get("target_id"),
            components=data.get("components"),
        )

    def _interaction_data_to_dict(self, data: InteractionData) -> Dict[str, Any]:
        """Convert interaction data to dict."""
        result = {}

        if data.id:
            result["id"] = str(data.id)
        if data.name:
            result["name"] = data.name
        if data.command_type:
            result["type"] = data.command_type.value if isinstance(data.command_type, CommandType) else data.command_type
        if data.resolved:
            result["resolved"] = data.resolved
        if data.options:
            result["options"] = data.options
        if data.custom_id:
            result["custom_id"] = data.custom_id
        if data.component_type:
            result["component_type"] = data.component_type.value if isinstance(data.component_type, ComponentType) else data.component_type
        if data.values:
            result["values"] = data.values
        if data.target_id:
            result["target_id"] = str(data.target_id)
        if data.components:
            result["components"] = data.components

        return result

    def _row_to_interaction(self, row) -> Interaction:
        """Convert database row to Interaction."""
        data = None
        if row["data"]:
            data = self._parse_interaction_data(json.loads(row["data"]))

        return Interaction(
            id=row["id"],
            application_id=row["application_id"],
            interaction_type=InteractionType(row["interaction_type"]),
            data=data,
            server_id=row["server_id"],
            channel_id=row["channel_id"],
            user_id=row["user_id"],
            token="",
            version=row["version"],
            message_id=row["message_id"],
            locale=row["locale"],
            server_locale=row["server_locale"],
            created_at=row["created_at"],
            responded=bool(row["responded"]),
            token_hash=row["token_hash"],
        )
