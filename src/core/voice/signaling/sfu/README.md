# SFU Adapters

Selective Forwarding Unit integrations for voice/video.

## Backends

- `mediasoup.py` - Mediasoup SFU adapter
- `janus.py` - Janus WebRTC gateway adapter

## Usage

```python
from src.core.voice.signaling.sfu import create_adapter

adapter = create_adapter("mediasoup", api_url="http://localhost:3000")
transport = await adapter.create_transport(user_id)
producer = await adapter.create_producer(transport, kind="audio")
```

## Base Class

All adapters extend `SFUAdapter` with models for `SFUTransport`, `SFUProducer`, and `SFUConsumer`.
