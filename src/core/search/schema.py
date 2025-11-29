"""
Search database schema - Table definitions for search module.

Includes FTS5 virtual tables for full-text search and discovery tables.
"""

import utils.logger as logger


SCHEMA = """
-- Message search FTS5 virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS search_messages_fts USING fts5(
    message_id UNINDEXED,
    content,
    author_id UNINDEXED,
    author_username,
    conversation_id UNINDEXED,
    server_id UNINDEXED,
    channel_id UNINDEXED,
    created_at UNINDEXED,
    has_attachments UNINDEXED,
    has_embeds UNINDEXED,
    has_links UNINDEXED,
    mentions UNINDEXED,
    is_pinned UNINDEXED,
    tokenize='porter unicode61'
);

-- User search FTS5 virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS search_users_fts USING fts5(
    user_id UNINDEXED,
    username,
    display_name,
    is_bot UNINDEXED,
    tokenize='porter unicode61'
);

-- Server search FTS5 virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS search_servers_fts USING fts5(
    server_id UNINDEXED,
    name,
    description,
    tags,
    category UNINDEXED,
    member_count UNINDEXED,
    is_public UNINDEXED,
    tokenize='porter unicode61'
);

-- Message index metadata (for tracking indexed messages)
CREATE TABLE IF NOT EXISTS search_message_index (
    message_id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    server_id INTEGER,
    channel_id INTEGER,
    author_id INTEGER NOT NULL,
    indexed_at INTEGER NOT NULL,
    updated_at INTEGER
);

-- User index metadata
CREATE TABLE IF NOT EXISTS search_user_index (
    user_id INTEGER PRIMARY KEY,
    indexed_at INTEGER NOT NULL,
    updated_at INTEGER
);

-- Server index metadata
CREATE TABLE IF NOT EXISTS search_server_index (
    server_id INTEGER PRIMARY KEY,
    indexed_at INTEGER NOT NULL,
    updated_at INTEGER
);

-- Server discovery listings
CREATE TABLE IF NOT EXISTS search_server_listings (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL UNIQUE,
    category TEXT NOT NULL,
    description TEXT,
    tags TEXT,
    member_count INTEGER NOT NULL DEFAULT 0,
    online_count INTEGER NOT NULL DEFAULT 0,
    verification_level TEXT NOT NULL DEFAULT 'none',
    is_verified INTEGER NOT NULL DEFAULT 0,
    is_partnered INTEGER NOT NULL DEFAULT 0,
    listed_at INTEGER NOT NULL,
    bumped_at INTEGER NOT NULL,
    bump_count INTEGER NOT NULL DEFAULT 0,
    listed_by INTEGER NOT NULL
);

-- Server categories
CREATE TABLE IF NOT EXISTS search_categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT,
    position INTEGER NOT NULL DEFAULT 0
);

-- Bump history (for cooldown tracking)
CREATE TABLE IF NOT EXISTS search_bump_history (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    bumped_at INTEGER NOT NULL
);

-- Search history (for suggestions)
CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    query TEXT NOT NULL,
    search_type TEXT NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    searched_at INTEGER NOT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_search_msg_conv ON search_message_index(conversation_id);
CREATE INDEX IF NOT EXISTS idx_search_msg_server ON search_message_index(server_id);
CREATE INDEX IF NOT EXISTS idx_search_msg_author ON search_message_index(author_id);
CREATE INDEX IF NOT EXISTS idx_search_listings_category ON search_server_listings(category);
CREATE INDEX IF NOT EXISTS idx_search_listings_bumped ON search_server_listings(bumped_at DESC);
CREATE INDEX IF NOT EXISTS idx_search_listings_members ON search_server_listings(member_count DESC);
CREATE INDEX IF NOT EXISTS idx_search_bump_server ON search_bump_history(server_id);
CREATE INDEX IF NOT EXISTS idx_search_bump_user ON search_bump_history(user_id);
CREATE INDEX IF NOT EXISTS idx_search_history_user ON search_history(user_id);
"""


DEFAULT_CATEGORIES = [
    ("gaming", "Gaming", "Servers for gamers and gaming communities", "gamepad", 1),
    ("music", "Music", "Music discussion, sharing, and listening parties", "music", 2),
    ("entertainment", "Entertainment", "Movies, TV shows, anime, and more", "film", 3),
    ("education", "Education", "Learning, studying, and academic communities", "book", 4),
    ("science", "Science & Tech", "Science, technology, and programming", "flask", 5),
    ("creative", "Creative", "Art, design, writing, and creative projects", "palette", 6),
    ("social", "Social", "General hangout and social communities", "users", 7),
    ("sports", "Sports", "Sports discussion and fan communities", "trophy", 8),
    ("finance", "Finance", "Investing, trading, and financial discussion", "chart", 9),
    ("other", "Other", "Everything else", "grid", 10),
]


def create_tables(db):
    """Create all search tables including FTS5 virtual tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    
    for statement in statements:
        if statement:
            try:
                db.execute(statement)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.error(f"Failed to execute schema statement: {e}")
                    raise
    
    _seed_categories(db)
    
    logger.info("Search tables created successfully")


def _seed_categories(db):
    """Seed default categories if not present."""
    existing = db.fetch_one("SELECT COUNT(*) as count FROM search_categories")
    if existing and existing["count"] > 0:
        return
    
    for cat_id, name, description, icon, position in DEFAULT_CATEGORIES:
        try:
            db.execute(
                """INSERT OR IGNORE INTO search_categories 
                   (id, name, description, icon, position) 
                   VALUES (?, ?, ?, ?, ?)""",
                (cat_id, name, description, icon, position)
            )
        except Exception as e:
            logger.warning(f"Failed to seed category {cat_id}: {e}")
