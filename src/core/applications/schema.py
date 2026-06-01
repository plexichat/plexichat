"""
Application database schema - Table definitions for applications module.
"""

import utils.logger as logger


SCHEMA = """
-- Applications table
CREATE TABLE IF NOT EXISTS app_applications (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    icon_url TEXT,
    bot_id INTEGER,
    bot_public INTEGER NOT NULL DEFAULT 1,
    bot_require_code_grant INTEGER NOT NULL DEFAULT 0,
    terms_of_service_url TEXT,
    privacy_policy_url TEXT,
    redirect_uris TEXT NOT NULL DEFAULT '[]',
    interactions_endpoint_url TEXT,
    client_secret_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (owner_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    FOREIGN KEY (bot_id) REFERENCES auth_bots(id) ON DELETE SET NULL
);

-- Application commands table
CREATE TABLE IF NOT EXISTS app_commands (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    command_type INTEGER NOT NULL DEFAULT 1,
    server_id INTEGER,
    options TEXT NOT NULL DEFAULT '[]',
    default_member_permissions TEXT,
    dm_permission INTEGER NOT NULL DEFAULT 1,
    nsfw INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE,
    UNIQUE(application_id, name, server_id)
);

-- Application installations table
CREATE TABLE IF NOT EXISTS app_installations (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    installer_id INTEGER NOT NULL,
    permissions TEXT NOT NULL DEFAULT '0',
    scopes TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE,
    UNIQUE(application_id, server_id)
);

-- Server-approved bots table
CREATE TABLE IF NOT EXISTS app_approved_bots (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    application_id INTEGER NOT NULL,
    approved_by INTEGER NOT NULL,
    permissions TEXT NOT NULL DEFAULT '0',
    bot_name TEXT,
    bot_avatar_url TEXT,
    status TEXT NOT NULL DEFAULT 'approved',
    installed_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE,
    FOREIGN KEY (approved_by) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Bot approval requests table
CREATE TABLE IF NOT EXISTS app_bot_requests (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    application_id INTEGER NOT NULL,
    requester_id INTEGER NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by INTEGER,
    review_reason TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE,
    FOREIGN KEY (requester_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    FOREIGN KEY (reviewed_by) REFERENCES auth_users(id) ON DELETE SET NULL
);

-- Bot profile table
CREATE TABLE IF NOT EXISTS app_bot_profiles (
    application_id INTEGER PRIMARY KEY,
    description TEXT,
    short_description TEXT,
    avatar_url TEXT,
    banner_url TEXT,
    website_url TEXT,
    support_url TEXT,
    github_url TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    nsfw INTEGER NOT NULL DEFAULT 0,
    private INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE
);

-- OAuth2 authorization codes table
CREATE TABLE IF NOT EXISTS app_oauth_codes (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    code_hash TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '[]',
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- OAuth2 tokens table
CREATE TABLE IF NOT EXISTS app_oauth_tokens (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    access_token_hash TEXT NOT NULL,
    refresh_token_hash TEXT,
    scopes TEXT NOT NULL DEFAULT '[]',
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Interactions table
CREATE TABLE IF NOT EXISTS app_interactions (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL,
    interaction_type INTEGER NOT NULL,
    data TEXT,
    server_id INTEGER,
    channel_id INTEGER,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    message_id INTEGER,
    locale TEXT,
    server_locale TEXT,
    created_at INTEGER NOT NULL,
    responded INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE
);

-- Webhook deliveries table
CREATE TABLE IF NOT EXISTS app_webhook_deliveries (
    id INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL,
    interaction_id INTEGER NOT NULL,
    endpoint_url TEXT NOT NULL,
    -- request_body/response_body are legacy plaintext columns kept for backward compatibility;
    -- new writes go to request_body_encrypted / response_body_encrypted.
    request_body TEXT NOT NULL,
    response_status INTEGER,
    response_body TEXT,
    request_body_encrypted TEXT,
    response_body_encrypted TEXT,
    delivered_at INTEGER NOT NULL,
    success INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (application_id) REFERENCES app_applications(id) ON DELETE CASCADE,
    FOREIGN KEY (interaction_id) REFERENCES app_interactions(id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_app_applications_owner ON app_applications(owner_id);
CREATE INDEX IF NOT EXISTS idx_app_applications_bot ON app_applications(bot_id);
CREATE INDEX IF NOT EXISTS idx_app_commands_application ON app_commands(application_id);
CREATE INDEX IF NOT EXISTS idx_app_commands_server ON app_commands(server_id);
CREATE INDEX IF NOT EXISTS idx_app_commands_name ON app_commands(name);
CREATE INDEX IF NOT EXISTS idx_app_installations_application ON app_installations(application_id);
CREATE INDEX IF NOT EXISTS idx_app_installations_server ON app_installations(server_id);
CREATE INDEX IF NOT EXISTS idx_app_approved_bots_server ON app_approved_bots(server_id);
CREATE INDEX IF NOT EXISTS idx_app_approved_bots_application ON app_approved_bots(application_id);
CREATE INDEX IF NOT EXISTS idx_app_approved_bots_status ON app_approved_bots(status);
CREATE INDEX IF NOT EXISTS idx_app_bot_requests_server ON app_bot_requests(server_id);
CREATE INDEX IF NOT EXISTS idx_app_bot_requests_application ON app_bot_requests(application_id);
CREATE INDEX IF NOT EXISTS idx_app_bot_requests_requester ON app_bot_requests(requester_id);
CREATE INDEX IF NOT EXISTS idx_app_bot_requests_status ON app_bot_requests(status);
CREATE INDEX IF NOT EXISTS idx_app_oauth_codes_application ON app_oauth_codes(application_id);
CREATE INDEX IF NOT EXISTS idx_app_oauth_codes_user ON app_oauth_codes(user_id);
CREATE INDEX IF NOT EXISTS idx_app_oauth_codes_hash ON app_oauth_codes(code_hash);
CREATE INDEX IF NOT EXISTS idx_app_oauth_tokens_application ON app_oauth_tokens(application_id);
CREATE INDEX IF NOT EXISTS idx_app_oauth_tokens_user ON app_oauth_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_app_oauth_tokens_access ON app_oauth_tokens(access_token_hash);
CREATE INDEX IF NOT EXISTS idx_app_oauth_tokens_refresh ON app_oauth_tokens(refresh_token_hash);
CREATE INDEX IF NOT EXISTS idx_app_interactions_application ON app_interactions(application_id);
CREATE INDEX IF NOT EXISTS idx_app_interactions_user ON app_interactions(user_id);
CREATE INDEX IF NOT EXISTS idx_app_interactions_token ON app_interactions(token_hash);
CREATE INDEX IF NOT EXISTS idx_app_webhook_deliveries_application ON app_webhook_deliveries(application_id);
CREATE INDEX IF NOT EXISTS idx_app_webhook_deliveries_interaction ON app_webhook_deliveries(interaction_id);
"""


def create_tables(db):
    """Create all application tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]

    for statement in statements:
        if statement:
            try:
                converted = (
                    db.convert_schema(statement)
                    if hasattr(db, "convert_schema")
                    else statement
                )
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Application tables created successfully")


def drop_tables(db):
    """Drop all application tables. USE WITH CAUTION."""
    tables = [
        "app_webhook_deliveries",
        "app_interactions",
        "app_oauth_tokens",
        "app_oauth_codes",
        "app_bot_profiles",
        "app_bot_requests",
        "app_approved_bots",
        "app_installations",
        "app_commands",
        "app_applications",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {table}")
