"""
Tests for server onboarding flow and progress tracking.
"""

import pytest


@pytest.mark.servers
class TestOnboardingStart:
    """Tests for starting onboarding."""

    def test_start_onboarding(self, server_with_members):
        """Test starting onboarding for a user."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        progress = servers.start_onboarding(member_user.id, server.id)

        assert progress is not None
        assert progress.user_id == member_user.id
        assert progress.server_id == server.id
        assert progress.completed is False
        assert progress.completed_steps == []

    def test_start_onboarding_idempotent(self, server_with_members):
        """Test that starting onboarding twice returns existing progress."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        progress1 = servers.start_onboarding(member_user.id, server.id)
        progress2 = servers.start_onboarding(member_user.id, server.id)

        assert progress1.id == progress2.id

    def test_start_onboarding_requires_membership(self, server_with_members):
        """Test that starting onboarding requires server membership."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        with pytest.raises(servers.ServerNotFoundError):
            servers.start_onboarding(outsider.id, server.id)


@pytest.mark.servers
class TestOnboardingStepCompletion:
    """Tests for completing onboarding steps."""

    def test_complete_onboarding_step(self, server_with_members):
        """Test completing an onboarding step."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Read Rules",
        )

        servers.start_onboarding(member_user.id, server.id)

        progress = servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=step.id,
        )

        assert step.id in progress.completed_steps

    def test_complete_step_idempotent(self, server_with_members):
        """Test that completing a step twice is idempotent."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Read Rules",
        )

        servers.start_onboarding(member_user.id, server.id)

        progress1 = servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=step.id,
        )

        progress2 = servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=step.id,
        )

        assert progress1.completed_steps == progress2.completed_steps

    def test_complete_select_roles_assigns_roles(self, server_with_members):
        """Test that completing select roles step assigns selected roles."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        new_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Selectable Role",
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.SELECT_ROLES,
            title="Select Roles",
            options={"role_ids": [new_role.id]},
        )

        servers.start_onboarding(member_user.id, server.id)

        servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=step.id,
            response={"selected_roles": [new_role.id]},
        )

        member_roles = servers.get_member_roles(server.id, member_user.id)
        role_ids = [r.id for r in member_roles]

        assert new_role.id in role_ids

    def test_complete_nonexistent_step_fails(self, server_with_members):
        """Test that completing nonexistent step fails."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        servers.start_onboarding(member_user.id, server.id)

        with pytest.raises(servers.OnboardingStepNotFoundError):
            servers.complete_onboarding_step(
                user_id=member_user.id,
                server_id=server.id,
                step_id=999999999,
            )

    def test_complete_step_auto_starts_onboarding(self, server_with_members):
        """Test that completing a step auto-starts onboarding if not started."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Auto Start Step",
        )

        progress = servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=step.id,
        )

        assert progress is not None
        assert step.id in progress.completed_steps


@pytest.mark.servers
class TestOnboardingCompletion:
    """Tests for onboarding completion tracking."""

    def test_onboarding_completes_when_all_required_done(self, server_with_members):
        """Test that onboarding completes when all required steps are done."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        required_step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Required Step",
            required=True,
        )

        servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Optional Step",
            required=False,
        )

        servers.start_onboarding(member_user.id, server.id)

        progress = servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=required_step.id,
        )

        assert progress.completed is True
        assert progress.completed_at is not None

    def test_onboarding_not_complete_with_missing_required(self, server_with_members):
        """Test that onboarding is not complete with missing required steps."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        required_step1 = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Required Step 1",
            required=True,
        )

        servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Required Step 2",
            required=True,
        )

        servers.start_onboarding(member_user.id, server.id)

        progress = servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=required_step1.id,
        )

        assert progress.completed is False

    def test_onboarding_completes_with_no_required_steps(self, server_with_members):
        """Test onboarding with only optional steps."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        optional_step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Optional Step",
            required=False,
        )

        servers.start_onboarding(member_user.id, server.id)

        progress = servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=optional_step.id,
        )

        assert progress.completed is True


@pytest.mark.servers
class TestOnboardingProgress:
    """Tests for onboarding progress retrieval."""

    def test_get_onboarding_progress(self, server_with_members):
        """Test getting onboarding progress."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        servers.start_onboarding(member_user.id, server.id)

        progress = servers.get_onboarding_progress(member_user.id, server.id)

        assert progress is not None
        assert progress.user_id == member_user.id

    def test_get_progress_not_started(self, server_with_members):
        """Test getting progress when not started."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        progress = servers.get_onboarding_progress(member_user.id, server.id)

        assert progress is None

    def test_progress_tracks_multiple_steps(self, server_with_members):
        """Test that progress tracks multiple completed steps."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step1 = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Step 1",
        )

        step2 = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Step 2",
        )

        servers.start_onboarding(member_user.id, server.id)

        servers.complete_onboarding_step(member_user.id, server.id, step1.id)
        progress = servers.complete_onboarding_step(member_user.id, server.id, step2.id)

        assert step1.id in progress.completed_steps
        assert step2.id in progress.completed_steps


@pytest.mark.servers
class TestOnboardingReset:
    """Tests for resetting onboarding progress."""

    def test_reset_onboarding_progress(self, server_with_members):
        """Test resetting onboarding progress."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.CUSTOM,
            title="Reset Step",
        )

        servers.start_onboarding(member_user.id, server.id)
        servers.complete_onboarding_step(member_user.id, server.id, step.id)

        result = servers.reset_onboarding_progress(member_user.id, server.id)
        assert result is True

        progress = servers.get_onboarding_progress(member_user.id, server.id)
        assert progress is None

    def test_reset_nonexistent_progress(self, server_with_members):
        """Test resetting progress that doesn't exist."""
        server, owner, admin_user, member_user, outsider, admin_role, servers = (
            server_with_members
        )

        result = servers.reset_onboarding_progress(member_user.id, server.id)
        assert result is True


@pytest.mark.servers
class TestOnboardingIntegration:
    """Integration tests for complete onboarding flows."""

    def test_full_onboarding_flow(self, server_with_channels):
        """Test a complete onboarding flow."""
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

        servers.set_welcome_screen(
            user_id=owner.id,
            server_id=server.id,
            description="Welcome to our community!",
            welcome_channels=[
                {"channel_id": general.id, "description": "Chat here"},
                {"channel_id": announcements.id, "description": "News"},
            ],
        )

        rules_step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.READ_RULES,
            title="Read Our Rules",
            required=True,
        )

        role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Member",
        )

        roles_step = servers.create_onboarding_step(
            user_id=owner.id,
            server_id=server.id,
            step_type=servers.OnboardingStepType.SELECT_ROLES,
            title="Choose Your Interests",
            options={"role_ids": [role.id]},
        )

        welcome = servers.get_welcome_screen(server.id, member_user.id)
        assert welcome is not None

        steps = servers.get_onboarding_steps(member_user.id, server.id)
        assert len(steps) >= 2

        servers.start_onboarding(member_user.id, server.id)

        servers.complete_onboarding_step(member_user.id, server.id, rules_step.id)

        progress = servers.complete_onboarding_step(
            user_id=member_user.id,
            server_id=server.id,
            step_id=roles_step.id,
            response={"selected_roles": [role.id]},
        )

        assert progress.completed is True
        assert rules_step.id in progress.completed_steps
        assert roles_step.id in progress.completed_steps

        member_roles = servers.get_member_roles(server.id, member_user.id)
        role_ids = [r.id for r in member_roles]
        assert role.id in role_ids
