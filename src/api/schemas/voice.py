from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional

class ICEServerConfig(BaseModel):
    """Configuration for a single ICE server."""
    urls: List[str] = Field(..., description="List of STUN/TURN server URLs")
    username: Optional[str] = Field(None, description="Username for TURN server authentication")
    credential: Optional[str] = Field(None, description="Credential for TURN server authentication")

class ICEServersResponse(BaseModel):
    """Response for ICE server configuration."""
    ice_servers: List[ICEServerConfig] = Field(..., description="List of ICE server configurations")

class VoiceChannelInfoResponse(BaseModel):
    """Response for voice channel connection info."""
    model_config = ConfigDict(from_attributes=True)

    channel_id: str = Field(..., description="The ID of the voice channel")
    session_id: str = Field(..., description="The session ID for the voice connection")
    ice_servers: List[ICEServerConfig] = Field(..., description="List of ICE server configurations")
    bitrate: int = Field(..., description="The recommended bitrate for the voice connection")
