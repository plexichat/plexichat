def up(db):
    if db.type != "sqlite":
        return

    db.execute(
        """CREATE TABLE IF NOT EXISTS _user_last_chat_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            last_message_id INTEGER,
            scroll_position INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL
        )"""
    )

    db.execute(
        """INSERT INTO _user_last_chat_new (id, user_id, conversation_id, last_message_id, scroll_position, updated_at)
           SELECT id, user_id, conversation_id, last_message_id,
                  COALESCE(scroll_position, 0), COALESCE(updated_at, 0)
           FROM user_last_chat"""
    )

    db.execute("DROP TABLE user_last_chat")
    db.execute("ALTER TABLE _user_last_chat_new RENAME TO user_last_chat")

    db.execute(
        """CREATE TABLE IF NOT EXISTS _user_recent_chats_new (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            conversation_id INTEGER NOT NULL,
            accessed_at INTEGER NOT NULL,
            unread_count INTEGER DEFAULT 0
        )"""
    )

    db.execute(
        """INSERT INTO _user_recent_chats_new (id, user_id, conversation_id, accessed_at, unread_count)
           SELECT id, user_id, conversation_id,
                  COALESCE(accessed_at, 0), COALESCE(unread_count, 0)
           FROM user_recent_chats"""
    )

    db.execute("DROP TABLE user_recent_chats")
    db.execute("ALTER TABLE _user_recent_chats_new RENAME TO user_recent_chats")


def down(db):
    return
