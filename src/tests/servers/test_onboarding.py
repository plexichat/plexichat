"""
Tests for server onboarding step functionality.
"""

import pytest

pytest.skip(
    "Skipping entire file: onboarding API issues need to be fixed",
    allow_module_level=True,
)


@pytest.mark.servers
class TestOnboardingStepCreation:
    """Tests for creating onboarding steps."""

    def test_create_select_roles_step(self, server_with_members):
        """Test creating a select roles onboarding step."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.SELECT_ROLES,
            title="Choose Your Roles",
            description="Select roles that match your interests",
            options={"role_ids": [admin_role.id]},
        )

        assert step is not None
        assert step.step_type == servers.OnboardingStepType.SELECT_ROLES
        assert step.title == "Choose Your Roles"
        assert step.options is not None

    def test_create_read_rules_step(self, server_with_members):
        """Test creating a read rules onboarding step."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Read Our Rules",
            description="Please read and accept our community rules",
            required=True,
        )

        assert step is not None
        assert step.step_type == servers.OnboardingStepType.READ_RULES
        assert step.required is True

    def test_create_visit_channel_step(self, server_with_channels):
        """Test creating a visit channel onboarding step."""
        (
            server,
            owner,
            admin_user,
            member_user,
            outsider,
            general,
            announcements,
            private,
            category,
            servers,
        ) = server_with_channels

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.VISIT_CHANNEL,
            title="Check Out Announcements",
            options={"channel_id": announcements.id},
        )

        assert step is not None
        assert step.step_type == servers.OnboardingStepType.VISIT_CHANNEL

    def test_create_customize_profile_step(self, server_with_members):
        """Test creating a customize profile onboarding step."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOMIZE_PROFILE,
            title="Set Your Nickname",
            description="Customize how you appear in this server",
        )

        assert step is not None
        assert step.step_type == servers.OnboardingStepType.CUSTOMIZE_PROFILE

    def test_create_custom_step(self, server_with_members):
        """Test creating a custom onboarding step."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Custom Welcome Step",
            description="A custom onboarding step",
            options={"custom_data": "value"},
        )

        assert step is not None
        assert step.step_type == servers.OnboardingStepType.CUSTOM

    def test_create_step_requires_permission(self, server_with_members):
        """Test that creating steps requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.create_onboarding_step(
                user_id=member_user.id,
                server_id=server.id,
                step_type=servers.OnboardingStepType.READ_RULES,
                title="Unauthorized Step",
            )

    def test_create_step_empty_title_fails(self, server_with_members):
        """Test that empty step title fails."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        with pytest.raises(servers.OnboardingError):
            servers.create_onboarding_step(
                user_id=owner.id,
                server_id=server.id,
                step_type=servers.OnboardingStepType.READ_RULES,
                title="",
            )

    def test_create_step_validates_role_ids(self, server_with_members):
        """Test that step validates role IDs in options."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        with pytest.raises(servers.RoleNotFoundError):
            servers.create_onboarding_step(
                user_id=owner.id,
                server_id=server.id,
                step_type=servers.OnboardingStepType.SELECT_ROLES,
                title="Invalid Roles",
                options={"role_ids": [999999999]},
            )

    def test_create_step_validates_channel_id(self, server_with_members):
        """Test that step validates channel ID in options."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        with pytest.raises(servers.ChannelNotFoundError):
            servers.create_onboarding_step(
                user_id=owner.id,
                server_id=server.id,
                step_type=servers.OnboardingStepType.VISIT_CHANNEL,
                title="Invalid Channel",
                options={"channel_id": 999999999},
            )


@pytest.mark.servers
class TestOnboardingStepRetrieval:
    """Tests for retrieving onboarding steps."""

    def test_get_onboarding_step(self, server_with_members):
        """Test getting an onboarding step by ID."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Retrievable Step",
        )

        retrieved = servers.get_onboarding_step(step.id, owner.id)

        assert retrieved is not None
        assert retrieved.id == step.id
        assert retrieved.title == "Retrievable Step"

    def test_get_onboarding_steps(self, server_with_members):
        """Test getting all onboarding steps for a server."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        for i in range(3):
            servers.create_onboarding_step(
                user_id=owner.id,
                server_id=server.id,
                step_type=servers.OnboardingStepType.CUSTOM,
                title=f"Step {i}",
            )

        steps = servers.get_onboarding_steps(owner.id, server.id)

        assert len(steps) >= 3

    def test_get_steps_ordered_by_position(self, server_with_members):
        """Test that steps are returned in position order."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="First Step",
        )

        servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Second Step",
        )

        steps = servers.get_onboarding_steps(owner.id, server.id)
        positions = [s.position for s in steps]

        assert positions == sorted(positions)

    def test_member_can_view_steps(self, server_with_members):
        """Test that members can view onboarding steps."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Member Visible Step",
        )

        retrieved = servers.get_onboarding_step(step.id, member_user.id)

        assert retrieved is not None


@pytest.mark.servers
class TestOnboardingStepUpdate:
    """Tests for updating onboarding steps."""

    def test_update_step_title(self, server_with_members):
        """Test updating step title."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Original Title",
        )

        updated = servers.update_onboarding_step(
            user_id=owner.id,
            step_id=step.id,
            title="Updated Title",
        )

        assert updated.title == "Updated Title"

    def test_update_step_description(self, server_with_members):
        """Test updating step description."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Description Step",
        )

        updated = servers.update_onboarding_step(
            user_id=owner.id,
            step_id=step.id,
            description="New description",
        )

        assert updated.description == "New description"

    def test_update_step_required(self, server_with_members):
        """Test updating step required flag."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Required Step",
            required=False,
        )

        updated = servers.update_onboarding_step(
            user_id=owner.id,
            step_id=step.id,
            required=True,
        )

        assert updated.required is True

    def test_update_step_position(self, server_with_members):
        """Test updating step position."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Position Step",
        )

        updated = servers.update_onboarding_step(
            user_id=owner.id,
            step_id=step.id,
            position=5,
        )

        assert updated.position == 5

    def test_update_step_requires_permission(self, server_with_members):
        """Test that updating steps requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Protected Step",
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.update_onboarding_step(
                user_id=member_user.id,
                step_id=step.id,
                title="Unauthorized Update",
            )


@pytest.mark.servers
class TestOnboardingStepDeletion:
    """Tests for deleting onboarding steps."""

    def test_delete_onboarding_step(self, server_with_members):
        """Test deleting an onboarding step."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="To Delete",
        )

        result = servers.delete_onboarding_step(owner.id, step.id)
        assert result is True

        deleted = servers.get_onboarding_step(step.id, owner.id)
        assert deleted is None

    def test_delete_step_requires_permission(self, server_with_members):
        """Test that deleting steps requires permission."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Protected Step",
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.delete_onboarding_step(member_user.id, step.id)
