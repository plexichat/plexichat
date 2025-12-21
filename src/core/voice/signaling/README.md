# Voice Signaling

WebRTC signaling for voice/video connections.

## Features

- SDP offer/answer exchange
- ICE candidate relay
- TURN credential generation
- SFU integration (mediasoup, Janus)
- Screen sharing support
- Connection quality monitoring

## Usage

```python
from src.core.voice import signaling

signaling.setup(voice_module, events_module, sfu_backend="mediasoup")

info = signaling.get_voice_server_info(user_id, channel_id)
answer = signaling.handle_sdp_offer(user_id, channel_id, sdp_offer)
signaling.handle_ice_candidate(user_id, channel_id, candidate)
```

## Components

- `manager.py` - SignalingManager
- `models.py` - Data models (SDP, ICE, TURN, etc.)
- `ice.py` - ICE candidate handling
- `sdp.py` - SDP parsing/validation
- `turn.py` - TURN credential generation
- `sfu/` - SFU adapter implementations
