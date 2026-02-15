"""
Initialize default automod rules for a specific server.
"""

import os
import sys
from pathlib import Path

# Setup paths
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, "src"))

from src.core.database import Database
from src.core.automod.manager import AutoModManager
from src.core.automod.models import RuleType, ActionType
import utils.config as config

def main():
    server_id = 269096246901284864
    user_id = 0  # System/Admin user
    
    # Initialize DB and Manager
    db = Database()
    # Mock modules for manager init
    manager = AutoModManager(db)
    
    print(f"Initializing default automod rules for server: {server_id}")
    
    # 1. Anti-Spam Rule
    try:
        manager.create_rule(
            user_id=user_id,
            server_id=server_id,
            name="Anti-Spam",
            rule_type=RuleType.MESSAGE_SPAM,
            rule_config={
                "max_messages": 5,
                "window_seconds": 10,
                "duplicate_threshold": 3,
                "similarity_threshold": 0.9
            },
            actions=[
                {"type": "delete_message"},
                {"type": "alert_moderators", "reason": "User is spamming"}
            ],
            priority=100
        )
        print("Created Anti-Spam rule.")
    except Exception as e:
        print(f"Error creating Anti-Spam rule: {e}")

    # 2. Hate Speech / Racist Language Filter
    # Note: Using placeholders and common patterns. 
    # For testing, we'll use 'trigger_racist_filter' as a keyword.
    try:
        manager.create_rule(
            user_id=user_id,
            server_id=server_id,
            name="Hate Speech Filter",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["trigger_racist_filter", "racist_slur_placeholder"],
                "whole_word": True,
                "case_sensitive": False
            },
            actions=[
                {"type": "delete_message"},
                {"type": "timeout_user", "duration_seconds": 3600, "reason": "Hate speech is not allowed"}
            ],
            priority=200
        )
        print("Created Hate Speech Filter rule.")
    except Exception as e:
        print(f"Error creating Hate Speech Filter: {e}")

    db.close()
    print("Initialization complete.")

if __name__ == "__main__":
    main()
