"""
Message spam detection rule - Detects rapid message sending and duplicate content.
"""

import hashlib
import time
from typing import Dict, Any, List

from .base import BaseRule, RuleMatch
from ..models import Rule, ViolationSeverity


class MessageSpamRule(BaseRule):
    """Rule that detects message spam (rate and duplicate content)."""
    
    def __init__(self, rule: Rule):
        super().__init__(rule)
        self._max_messages = self.config.get("max_messages", 5)
        self._time_window_seconds = self.config.get("time_window_seconds", 5)
        self._duplicate_threshold = self.config.get("duplicate_threshold", 3)
        self._duplicate_window_seconds = self.config.get("duplicate_window_seconds", 60)
    
    def check(self, content: str, context: Dict[str, Any]) -> RuleMatch:
        """Check for message spam."""
        db = context.get("db")
        user_id = context.get("user_id")
        server_id = context.get("server_id")
        channel_id = context.get("channel_id")
        
        if not all([db, user_id, server_id]):
            return RuleMatch(matched=False)
        
        now = int(time.time() * 1000)
        content_hash = self._hash_content(content)
        
        rate_violation = self._check_rate_limit(db, user_id, server_id, now)
        duplicate_violation = self._check_duplicates(db, user_id, server_id, content_hash, now)
        
        self._record_message(db, user_id, server_id, channel_id, context.get("message_id", 0), content_hash, now)
        
        if rate_violation or duplicate_violation:
            details = {}
            severity = ViolationSeverity.MEDIUM
            
            if rate_violation:
                details["rate_limit_exceeded"] = True
                details["messages_in_window"] = rate_violation
                severity = ViolationSeverity.HIGH
            
            if duplicate_violation:
                details["duplicate_detected"] = True
                details["duplicate_count"] = duplicate_violation
                if duplicate_violation >= self._duplicate_threshold * 2:
                    severity = ViolationSeverity.HIGH
            
            return RuleMatch(
                matched=True,
                severity=severity,
                matched_content="spam detected",
                trigger_details=details
            )
        
        return RuleMatch(matched=False)
    
    def _hash_content(self, content: str) -> str:
        """Create hash of content for duplicate detection."""
        normalized = content.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]
    
    def _check_rate_limit(self, db, user_id: int, server_id: int, now: int) -> int:
        """Check if user is sending messages too fast."""
        window_start = now - (self._time_window_seconds * 1000)
        
        row = db.fetch_one(
            """SELECT COUNT(*) as count FROM automod_message_history
               WHERE user_id = ? AND server_id = ? AND created_at > ?""",
            (user_id, server_id, window_start)
        )
        
        count = row["count"] if row else 0
        
        if count >= self._max_messages:
            return count
        return 0
    
    def _check_duplicates(self, db, user_id: int, server_id: int, content_hash: str, now: int) -> int:
        """Check for duplicate message content."""
        window_start = now - (self._duplicate_window_seconds * 1000)
        
        row = db.fetch_one(
            """SELECT COUNT(*) as count FROM automod_message_history
               WHERE user_id = ? AND server_id = ? AND content_hash = ? AND created_at > ?""",
            (user_id, server_id, content_hash, window_start)
        )
        
        count = row["count"] if row else 0
        
        if count >= self._duplicate_threshold:
            return count
        return 0
    
    def _record_message(self, db, user_id: int, server_id: int, channel_id: int, message_id: int, content_hash: str, now: int):
        """Record message for spam tracking."""
        from src.utils.encryption import generate_snowflake_id
        
        record_id = generate_snowflake_id()
        db.execute(
            """INSERT INTO automod_message_history 
               (id, server_id, channel_id, user_id, message_id, content_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record_id, server_id, channel_id, user_id, message_id, content_hash, now)
        )
        
        cleanup_threshold = now - (max(self._time_window_seconds, self._duplicate_window_seconds) * 2 * 1000)
        db.execute(
            "DELETE FROM automod_message_history WHERE created_at < ?",
            (cleanup_threshold,)
        )
    
    @classmethod
    def validate_config(cls, config: Dict[str, Any]) -> List[str]:
        """Validate spam rule configuration."""
        issues = []
        
        max_messages = config.get("max_messages", 5)
        if not isinstance(max_messages, int) or max_messages < 1:
            issues.append("max_messages must be a positive integer")
        elif max_messages > 100:
            issues.append("max_messages cannot exceed 100")
        
        time_window = config.get("time_window_seconds", 5)
        if not isinstance(time_window, int) or time_window < 1:
            issues.append("time_window_seconds must be a positive integer")
        elif time_window > 3600:
            issues.append("time_window_seconds cannot exceed 3600")
        
        dup_threshold = config.get("duplicate_threshold", 3)
        if not isinstance(dup_threshold, int) or dup_threshold < 1:
            issues.append("duplicate_threshold must be a positive integer")
        elif dup_threshold > 50:
            issues.append("duplicate_threshold cannot exceed 50")
        
        dup_window = config.get("duplicate_window_seconds", 60)
        if not isinstance(dup_window, int) or dup_window < 1:
            issues.append("duplicate_window_seconds must be a positive integer")
        elif dup_window > 86400:
            issues.append("duplicate_window_seconds cannot exceed 86400")
        
        return issues
    
    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "max_messages": 5,
            "time_window_seconds": 5,
            "duplicate_threshold": 3,
            "duplicate_window_seconds": 60,
        }
