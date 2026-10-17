import json

from ..models import (
    Application,
    ApplicationInstallation,
    ApprovedBot,
    BotRequest,
    BotProfile,
    BotApprovalStatus,
)


def row_to_application(row) -> Application:
    redirect_uris = json.loads(row["redirect_uris"]) if row["redirect_uris"] else []
    return Application(
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        description=row["description"],
        icon_url=row["icon_url"],
        bot_id=row["bot_id"],
        bot_public=bool(row["bot_public"]),
        bot_require_code_grant=bool(row["bot_require_code_grant"]),
        terms_of_service_url=row["terms_of_service_url"],
        privacy_policy_url=row["privacy_policy_url"],
        redirect_uris=redirect_uris,
        interactions_endpoint_url=row["interactions_endpoint_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_installation(row) -> ApplicationInstallation:
    scopes = json.loads(row["scopes"]) if row["scopes"] else []
    return ApplicationInstallation(
        id=row["id"],
        application_id=row["application_id"],
        server_id=row["server_id"],
        installer_id=row["installer_id"],
        permissions=row["permissions"],
        scopes=scopes,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_approved_bot(row) -> ApprovedBot:
    return ApprovedBot(
        id=row["id"],
        server_id=row["server_id"],
        application_id=row["application_id"],
        approved_by=row["approved_by"],
        permissions=row["permissions"],
        bot_name=row["bot_name"],
        bot_avatar_url=row["bot_avatar_url"],
        status=BotApprovalStatus(row["status"]),
        installed_at=row["installed_at"],
        updated_at=row["updated_at"],
    )


def row_to_bot_request(row) -> BotRequest:
    return BotRequest(
        id=row["id"],
        server_id=row["server_id"],
        application_id=row["application_id"],
        requester_id=row["requester_id"],
        reason=row["reason"],
        status=BotApprovalStatus(row["status"]),
        reviewed_by=row["reviewed_by"],
        review_reason=row["review_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_bot_profile(row) -> BotProfile:
    tags = json.loads(row["tags"]) if row["tags"] else []
    return BotProfile(
        application_id=row["application_id"],
        description=row["description"],
        short_description=row["short_description"],
        avatar_url=row["avatar_url"],
        banner_url=row["banner_url"],
        website_url=row["website_url"],
        support_url=row["support_url"],
        github_url=row["github_url"],
        tags=tags,
        nsfw=bool(row["nsfw"]) if row["nsfw"] else False,
        private=bool(row["private"]) if row["private"] else False,
        updated_at=row["updated_at"],
    )
