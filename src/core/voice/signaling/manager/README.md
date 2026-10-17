# Signaling Manager

Mixin-based architecture for WebRTC signaling operations.

## Structure

- `base.py` - `SignalingManagerBase`: Core initialization and shared state
- `connection_mixin.py` - `ConnectionManagementMixin`: Connection lifecycle
- `sdp_mixin.py` - `SDPMixin`: SDP offer/answer handling
- `ice_mixin.py` - `ICEMixin`: ICE candidate processing
- `turn_mixin.py` - `TURNMixin`: TURN credential management
- `screenshare_mixin.py` - `ScreenShareMixin`: Screen share handling
- `quality_mixin.py` - `QualityMixin`: Quality monitoring
- `composer.py` - Combines all mixins into `SignalingManager`

## Usage

```python
from src.core.voice.signaling import setup

setup(sfu_backend="aiortc")
```