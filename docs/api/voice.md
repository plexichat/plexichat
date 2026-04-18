# Voice Routes

The REST voice routes provide signaling metadata rather than media transport.

## Routes

- `GET /voice/ice-servers`
- `GET /voice/channels/{channel_id}/info`

## Purpose

- discover ICE server configuration that the client may use for WebRTC negotiation
- fetch voice-channel connection metadata for a specific channel

## SFU Backend

Plexichat uses aiortc as the default SFU (Selective Forwarding Unit) backend. aiortc is a pure Python WebRTC implementation that runs in-process with the FastAPI application, providing:

- **Integrated deployment**: No external SFU service required
- **Full Python control**: Complete visibility into media routing
- **Flexibility**: Easy to customize and extend

Alternative backends are available for specific use cases:
- `mediasoup-ws`: WebSocket-based adapter for mediasoup-demo server
- `mediasoup`: REST API adapter for custom mediasoup servers
- `janus`: REST API adapter for Janus Gateway

Configure the SFU backend via the `voice.sfu_backend` config option (default: `aiortc`).

## Notes

- actual media transport is negotiated outside these REST endpoints
- the WebSocket gateway carries related voice signaling opcodes
- deployments can expose different ICE/TURN settings, so clients should treat the response as runtime data rather than static config

## Client Guidance

- request ICE servers from the backend instead of hardcoding them
- combine these routes with gateway voice events and voice opcodes
- fail gracefully when voice is disabled or unsupported on a given server

