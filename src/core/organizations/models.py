"""
Organization data models.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class OrgRole(Enum):
    """Organization member roles."""
    ROOT = "root"
    ADMIN = "admin"
    MEMBER = "member"


@dataclass
class Organization:
    """Represents an organization."""
    id: int
    name: str
    display_name: str
    root_user_id: int
    is_default: bool = False
    created_at: int = 0
    updated_at: int = 0
    settings: Dict[str, Any] = field(default_factory=dict)
    default_servers: List[int] = field(default_factory=list)
    allowed_servers: Optional[List[int]] = None  # None = all allowed
    blocked_servers: List[int] = field(default_factory=list)
    allow_invites: bool = True
    invite_requires_approval: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "name": self.name,
            "display_name": self.display_name,
            "root_user_id": str(self.root_user_id),
            "is_default": self.is_default,
            "created_at": self.created_at,
            "allow_invites": self.allow_invites,
        }


@dataclass
class OrgMember:
    """Represents a member of an organization."""
    id: int
    org_id: int
    user_id: int
    role: OrgRole
    joined_at: int = 0
    invited_by: Optional[int] = None
    username: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "user_id": str(self.user_id),
            "username": self.username,
            "role": self.role.value,
            "joined_at": self.joined_at,
        }


@dataclass
class OrgInvite:
    """Represents an organization invite."""
    id: int
    org_id: int
    code: str
    invite_type: str  # "existing" or "registration"
    target_username: Optional[str] = None
    target_user_id: Optional[int] = None
    created_by: int = 0
    created_at: int = 0
    expires_at: Optional[int] = None
    max_uses: int = 1
    uses: int = 0
    status: str = "pending"  # pending, completed, rejected, expired
    user_accepted: bool = False
    user_accepted_at: Optional[int] = None
    root_approved: bool = False
    root_approved_at: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "code": self.code,
            "invite_type": self.invite_type,
            "target_username": self.target_username,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "user_accepted": self.user_accepted,
            "root_approved": self.root_approved,
        }


@dataclass
class OrgManagedSetting:
    """Represents an org-managed user setting."""
    id: int
    org_id: int
    setting_key: str
    setting_value: Optional[str] = None
    locked: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "key": self.setting_key,
            "value": self.setting_value,
            "locked": self.locked,
        }
