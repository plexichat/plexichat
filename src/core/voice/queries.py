from typing import Optional, List
from ..base import SnowflakeID


def is_user_in_voice(db, user_id: SnowflakeID) -> bool:
    row = db.fetch_one("SELECT 1 FROM voice_states WHERE user_id = ?", (user_id,))
    return row is not None


def get_user_channel(db, user_id: SnowflakeID) -> Optional[SnowflakeID]:
    row = db.fetch_one(
        "SELECT channel_id FROM voice_states WHERE user_id = ?", (user_id,)
    )
    return row["channel_id"] if row else None


def get_channel_members(db, channel_id: SnowflakeID) -> List[SnowflakeID]:
    rows = db.fetch_all(
        "SELECT user_id FROM voice_states WHERE channel_id = ?", (channel_id,)
    )
    return [row["user_id"] for row in rows]
