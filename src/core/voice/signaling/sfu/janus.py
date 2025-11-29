"""
Janus SFU adapter - Integration with Janus WebRTC Gateway.

Makes actual HTTP requests to the Janus API.
"""

import asyncio
import secrets
import time
from typing import Dict, List, Optional, Any

import utils.logger as logger

from ..exceptions import SFUConnectionError, SFUTimeoutError
from .base import (
    SFUAdapter,
    SFUTransport,
    SFUProducer,
    SFUConsumer,
    RoomInfo,
    TransportDirection,
    MediaKind,
)


class JanusAdapter(SFUAdapter):
    """
    Adapter for Janus WebRTC Gateway.
    
    Uses the Janus REST API with the VideoRoom plugin for SFU functionality.
    """
    
    def __init__(self, api_url: str, timeout: int = 10, api_secret: str = ""):
        """
        Initialize the Janus adapter.
        
        Args:
            api_url: Base URL of the Janus API
            timeout: Request timeout in seconds
            api_secret: Optional API secret for authentication
        """
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._api_secret = api_secret
        self._session = None
        self._janus_sessions: Dict[str, int] = {}  # room_id -> session_id
        self._janus_handles: Dict[str, Dict[str, int]] = {}  # room_id -> {peer_id -> handle_id}
    
    async def _get_session(self):
        """Get or create aiohttp session."""
        if self._session is None:
            try:
                import aiohttp
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                )
            except ImportError:
                raise SFUConnectionError(
                    "aiohttp is required for Janus adapter",
                    backend="janus",
                    url=self._api_url
                )
        return self._session
    
    def _generate_transaction(self) -> str:
        """Generate a unique transaction ID."""
        return secrets.token_hex(12)
    
    async def _request(
        self,
        endpoint: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Make an HTTP request to the Janus API.
        
        Args:
            endpoint: API endpoint
            data: Request body data
            
        Returns:
            Response JSON
        """
        session = await self._get_session()
        url = f"{self._api_url}{endpoint}"
        
        # Add transaction ID
        data["transaction"] = self._generate_transaction()
        
        # Add API secret if configured
        if self._api_secret:
            data["apisecret"] = self._api_secret
        
        try:
            async with session.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
            ) as response:
                result = await response.json()
                
                if result.get("janus") == "error":
                    error = result.get("error", {})
                    raise SFUConnectionError(
                        f"Janus error: {error.get('reason', 'Unknown')}",
                        backend="janus",
                        url=url
                    )
                
                return result
                
        except asyncio.TimeoutError:
            raise SFUTimeoutError(
                f"Janus request timed out",
                operation=endpoint,
                timeout_ms=self._timeout * 1000
            )
        except Exception as e:
            if isinstance(e, (SFUConnectionError, SFUTimeoutError)):
                raise
            raise SFUConnectionError(
                f"Janus connection failed: {e}",
                backend="janus",
                url=url
            )
    
    async def _create_janus_session(self) -> int:
        """Create a Janus session."""
        result = await self._request("", {"janus": "create"})
        return result["data"]["id"]
    
    async def _attach_plugin(self, session_id: int, plugin: str = "janus.plugin.videoroom") -> int:
        """Attach to a Janus plugin."""
        result = await self._request(
            f"/{session_id}",
            {
                "janus": "attach",
                "plugin": plugin,
            }
        )
        return result["data"]["id"]
    
    async def _send_message(
        self,
        session_id: int,
        handle_id: int,
        body: Dict[str, Any],
        jsep: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a message to a plugin handle."""
        data = {
            "janus": "message",
            "body": body,
        }
        if jsep:
            data["jsep"] = jsep
        
        result = await self._request(f"/{session_id}/{handle_id}", data)
        return result
    
    async def create_room(self, room_id: str) -> RoomInfo:
        """Create a new room on the Janus server."""
        # Create Janus session for this room
        session_id = await self._create_janus_session()
        handle_id = await self._attach_plugin(session_id)
        
        self._janus_sessions[room_id] = session_id
        self._janus_handles[room_id] = {"admin": handle_id}
        
        # Create VideoRoom
        room_num = abs(hash(room_id)) % (10**9)  # Convert to numeric ID
        
        result = await self._send_message(
            session_id,
            handle_id,
            {
                "request": "create",
                "room": room_num,
                "description": room_id,
                "publishers": 100,
                "bitrate": 128000,
                "fir_freq": 10,
                "audiocodec": "opus",
                "videocodec": "vp8,h264",
            }
        )
        
        logger.debug(f"Created Janus room: {room_id} (numeric: {room_num})")
        
        return RoomInfo(id=room_id, peers=[], producers=[])
    
    async def close_room(self, room_id: str) -> bool:
        """Close a room on the Janus server."""
        session_id = self._janus_sessions.get(room_id)
        if not session_id:
            return False
        
        handle_id = self._janus_handles.get(room_id, {}).get("admin")
        if handle_id:
            room_num = abs(hash(room_id)) % (10**9)
            await self._send_message(
                session_id,
                handle_id,
                {
                    "request": "destroy",
                    "room": room_num,
                }
            )
        
        # Destroy session
        await self._request(f"/{session_id}", {"janus": "destroy"})
        
        del self._janus_sessions[room_id]
        if room_id in self._janus_handles:
            del self._janus_handles[room_id]
        
        logger.debug(f"Closed Janus room: {room_id}")
        return True
    
    async def join_room(self, room_id: str, peer_id: str) -> Dict[str, Any]:
        """Join a peer to a room."""
        session_id = self._janus_sessions.get(room_id)
        if not session_id:
            raise SFUConnectionError(
                f"Room {room_id} not found",
                backend="janus",
                url=self._api_url
            )
        
        # Create handle for this peer
        handle_id = await self._attach_plugin(session_id)
        
        if room_id not in self._janus_handles:
            self._janus_handles[room_id] = {}
        self._janus_handles[room_id][peer_id] = handle_id
        
        room_num = abs(hash(room_id)) % (10**9)
        peer_num = abs(hash(peer_id)) % (10**9)
        
        # Join as publisher
        result = await self._send_message(
            session_id,
            handle_id,
            {
                "request": "join",
                "room": room_num,
                "ptype": "publisher",
                "id": peer_num,
                "display": peer_id,
            }
        )
        
        plugindata = result.get("plugindata", {}).get("data", {})
        
        logger.debug(f"Peer {peer_id} joined Janus room {room_id}")
        
        return {
            "routerRtpCapabilities": {},  # Janus handles this differently
            "peers": [p.get("display", str(p.get("id"))) for p in plugindata.get("publishers", [])],
            "producers": [],
        }
    
    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """Remove a peer from a room."""
        session_id = self._janus_sessions.get(room_id)
        handle_id = self._janus_handles.get(room_id, {}).get(peer_id)
        
        if session_id and handle_id:
            await self._send_message(
                session_id,
                handle_id,
                {"request": "leave"}
            )
            
            # Detach handle
            await self._request(
                f"/{session_id}/{handle_id}",
                {"janus": "detach"}
            )
            
            del self._janus_handles[room_id][peer_id]
        
        logger.debug(f"Peer {peer_id} left Janus room {room_id}")
        return True
    
    async def create_transport(
        self,
        room_id: str,
        peer_id: str,
        direction: TransportDirection,
    ) -> SFUTransport:
        """Create a WebRTC transport for a peer."""
        # Janus handles transport creation implicitly during publish/subscribe
        # Return a placeholder transport
        transport_id = f"{room_id}_{peer_id}_{direction.value}_{int(time.time() * 1000)}"
        
        return SFUTransport(
            id=transport_id,
            direction=direction,
            ice_parameters={},
            ice_candidates=[],
            dtls_parameters={},
        )
    
    async def connect_transport(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        dtls_parameters: Dict[str, Any],
    ) -> bool:
        """Connect a transport with DTLS parameters."""
        # Janus handles this during configure
        return True
    
    async def produce(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        kind: MediaKind,
        rtp_parameters: Dict[str, Any],
    ) -> SFUProducer:
        """Create a producer to send media."""
        session_id = self._janus_sessions.get(room_id)
        handle_id = self._janus_handles.get(room_id, {}).get(peer_id)
        
        if not session_id or not handle_id:
            raise SFUConnectionError(
                f"Peer {peer_id} not in room {room_id}",
                backend="janus",
                url=self._api_url
            )
        
        # Configure publishing
        result = await self._send_message(
            session_id,
            handle_id,
            {
                "request": "configure",
                "audio": kind == MediaKind.AUDIO,
                "video": kind == MediaKind.VIDEO,
            }
        )
        
        producer_id = f"{peer_id}_{kind.value}_{int(time.time() * 1000)}"
        
        return SFUProducer(
            id=producer_id,
            kind=kind,
            rtp_parameters=rtp_parameters,
            paused=False,
        )
    
    async def consume(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: Dict[str, Any],
    ) -> SFUConsumer:
        """Create a consumer to receive media."""
        session_id = self._janus_sessions.get(room_id)
        
        if not session_id:
            raise SFUConnectionError(
                f"Room {room_id} not found",
                backend="janus",
                url=self._api_url
            )
        
        # Create subscriber handle
        handle_id = await self._attach_plugin(session_id)
        
        room_num = abs(hash(room_id)) % (10**9)
        
        # Parse producer_id to get feed ID
        parts = producer_id.split("_")
        feed_peer = parts[0] if parts else producer_id
        feed_num = abs(hash(feed_peer)) % (10**9)
        
        # Join as subscriber
        result = await self._send_message(
            session_id,
            handle_id,
            {
                "request": "join",
                "room": room_num,
                "ptype": "subscriber",
                "feed": feed_num,
            }
        )
        
        consumer_id = f"{peer_id}_sub_{producer_id}_{int(time.time() * 1000)}"
        
        # Determine kind from producer_id
        kind = MediaKind.VIDEO if "video" in producer_id else MediaKind.AUDIO
        
        return SFUConsumer(
            id=consumer_id,
            producer_id=producer_id,
            kind=kind,
            rtp_parameters={},
            paused=False,
        )
    
    async def pause_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Pause a producer."""
        session_id = self._janus_sessions.get(room_id)
        handle_id = self._janus_handles.get(room_id, {}).get(peer_id)
        
        if session_id and handle_id:
            kind = "video" if "video" in producer_id else "audio"
            await self._send_message(
                session_id,
                handle_id,
                {
                    "request": "configure",
                    kind: False,
                }
            )
        return True
    
    async def resume_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Resume a producer."""
        session_id = self._janus_sessions.get(room_id)
        handle_id = self._janus_handles.get(room_id, {}).get(peer_id)
        
        if session_id and handle_id:
            kind = "video" if "video" in producer_id else "audio"
            await self._send_message(
                session_id,
                handle_id,
                {
                    "request": "configure",
                    kind: True,
                }
            )
        return True
    
    async def close_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Close a producer."""
        session_id = self._janus_sessions.get(room_id)
        handle_id = self._janus_handles.get(room_id, {}).get(peer_id)
        
        if session_id and handle_id:
            await self._send_message(
                session_id,
                handle_id,
                {"request": "unpublish"}
            )
        return True
    
    async def get_room_info(self, room_id: str) -> Optional[RoomInfo]:
        """Get information about a room."""
        session_id = self._janus_sessions.get(room_id)
        handle_id = self._janus_handles.get(room_id, {}).get("admin")
        
        if not session_id or not handle_id:
            return None
        
        room_num = abs(hash(room_id)) % (10**9)
        
        result = await self._send_message(
            session_id,
            handle_id,
            {
                "request": "listparticipants",
                "room": room_num,
            }
        )
        
        plugindata = result.get("plugindata", {}).get("data", {})
        participants = plugindata.get("participants", [])
        
        return RoomInfo(
            id=room_id,
            peers=[p.get("display", str(p.get("id"))) for p in participants],
            producers=[],
        )
    
    async def get_router_capabilities(self, room_id: str) -> Dict[str, Any]:
        """Get RTP capabilities for a room's router."""
        # Janus doesn't expose router capabilities the same way
        return {
            "codecs": [
                {"mimeType": "audio/opus", "clockRate": 48000, "channels": 2},
                {"mimeType": "video/VP8", "clockRate": 90000},
                {"mimeType": "video/H264", "clockRate": 90000},
            ]
        }
    
    async def set_preferred_layers(
        self,
        room_id: str,
        peer_id: str,
        consumer_id: str,
        spatial_layer: int,
        temporal_layer: int,
    ) -> bool:
        """Set preferred simulcast layers for a consumer."""
        # Janus VideoRoom handles simulcast differently
        return True
    
    async def health_check(self) -> bool:
        """Check if the Janus server is healthy."""
        try:
            result = await self._request("", {"janus": "info"})
            return result.get("janus") == "server_info"
        except Exception:
            return False
    
    async def close(self) -> None:
        """Close the HTTP session and cleanup."""
        # Destroy all sessions
        for room_id, session_id in list(self._janus_sessions.items()):
            try:
                await self._request(f"/{session_id}", {"janus": "destroy"})
            except Exception:
                pass
        
        self._janus_sessions.clear()
        self._janus_handles.clear()
        
        if self._session:
            await self._session.close()
            self._session = None
