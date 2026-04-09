def up(db):
    try:
        # Add max_reactions_per_message column to srv_servers
        db.execute(
            "ALTER TABLE srv_servers ADD COLUMN max_reactions_per_message INTEGER DEFAULT 20"
        )
    except Exception:
        # If column already exists or other error, ignore
        pass


def down(db):
    try:
        # PostgreSQL doesn't support dropping columns easily if it's not present
        db.execute(
            "ALTER TABLE srv_servers DROP COLUMN IF EXISTS max_reactions_per_message"
        )
    except Exception:
        pass
