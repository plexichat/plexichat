"""
Applications test fixtures.

Provides fixtures for testing the applications module including
applications, commands, interactions, and OAuth flows.
"""

import pytest
import uuid



@pytest.fixture
def app_factory(modules, user_pool):
    """Factory for creating test applications."""

    class ApplicationFactory:
        def __init__(self):
            self._count = 0

        def create(
            self,
            owner=None,
            name=None,
            description=None,
            redirect_uris=None,
        ):
            """Create a test application."""
            if owner is None:
                owner = user_pool.get_user()

            self._count += 1
            if name is None:
                name = f"Test App {self._count}_{uuid.uuid4().hex[:6]}"

            app = modules.applications.create_application(
                owner_id=owner.id,
                name=name,
                description=description or "Test application",
                redirect_uris=redirect_uris or ["https://example.com/callback"],
            )
            return app, owner

        def create_with_bot(self, owner=None, permissions=None):
            """Create application with bot account."""
            app, owner = self.create(owner=owner)

            bot_info = modules.applications.create_bot_for_application(
                user_id=owner.id,
                application_id=app.id,
                permissions=permissions,
            )

            app = modules.applications.get_application(app.id)
            return app, owner, bot_info

    return ApplicationFactory()


@pytest.fixture
def command_factory(modules, app_factory):
    """Factory for creating test commands."""

    class CommandFactory:
        def __init__(self):
            self._count = 0

        def create(
            self,
            application=None,
            owner=None,
            name=None,
            description=None,
            server_id=None,
            options=None,
        ):
            """Create a test command."""
            if application is None:
                application, owner = app_factory.create(owner=owner)

            self._count += 1
            if name is None:
                name = f"testcmd{self._count}"

            cmd = modules.applications.register_command(
                application_id=application.id,
                name=name,
                description=description or "Test command",
                server_id=server_id,
                options=options,
            )
            return cmd, application

        def create_with_options(self, application=None, owner=None):
            """Create command with various option types."""
            from src.core.applications import CommandOptionType

            options = [
                {
                    "name": "text",
                    "description": "Text input",
                    "type": CommandOptionType.STRING,
                    "required": True,
                },
                {
                    "name": "number",
                    "description": "Number input",
                    "type": CommandOptionType.INTEGER,
                    "required": False,
                },
                {
                    "name": "user",
                    "description": "User mention",
                    "type": CommandOptionType.USER,
                    "required": False,
                },
            ]

            return self.create(
                application=application,
                owner=owner,
                options=options,
            )

    return CommandFactory()


@pytest.fixture
def interaction_factory(modules, app_factory, user_pool):
    """Factory for creating test interactions."""

    class InteractionFactory:
        def __init__(self):
            self._count = 0

        def create(
            self,
            application=None,
            owner=None,
            user=None,
            interaction_type=None,
            data=None,
            server_id=None,
            channel_id=None,
        ):
            """Create a test interaction."""
            from src.core.applications import InteractionType

            if application is None:
                application, owner = app_factory.create(owner=owner)

            if user is None:
                user = user_pool.get_user()

            if interaction_type is None:
                interaction_type = InteractionType.APPLICATION_COMMAND

            self._count += 1

            interaction = modules.applications.handle_interaction(
                application_id=application.id,
                interaction_type=interaction_type,
                user_id=user.id,
                data=data or {"name": "test", "type": 1},
                server_id=server_id,
                channel_id=channel_id,
            )
            return interaction, application, user

        def create_command_interaction(self, command, user=None):
            """Create interaction for a specific command."""
            from src.core.applications import InteractionType

            if user is None:
                user = user_pool.get_user()

            interaction = modules.applications.handle_interaction(
                application_id=command.application_id,
                interaction_type=InteractionType.APPLICATION_COMMAND,
                user_id=user.id,
                data={
                    "id": command.id,
                    "name": command.name,
                    "type": command.command_type.value,
                },
            )
            return interaction, user

        def create_component_interaction(
            self,
            application=None,
            owner=None,
            user=None,
            custom_id="test_button",
            component_type=2,
        ):
            """Create a component interaction."""
            from src.core.applications import InteractionType

            if application is None:
                application, owner = app_factory.create(owner=owner)

            if user is None:
                user = user_pool.get_user()

            interaction = modules.applications.handle_interaction(
                application_id=application.id,
                interaction_type=InteractionType.MESSAGE_COMPONENT,
                user_id=user.id,
                data={
                    "custom_id": custom_id,
                    "component_type": component_type,
                },
            )
            return interaction, application, user

    return InteractionFactory()


@pytest.fixture
def test_application(app_factory):
    """Create a single test application."""
    app, owner = app_factory.create()
    return app, owner


@pytest.fixture
def test_application_with_bot(app_factory):
    """Create a test application with bot."""
    return app_factory.create_with_bot()


@pytest.fixture
def test_command(command_factory):
    """Create a single test command."""
    cmd, app = command_factory.create()
    return cmd, app


@pytest.fixture
def test_interaction(interaction_factory):
    """Create a single test interaction."""
    interaction, app, user = interaction_factory.create()
    return interaction, app, user
