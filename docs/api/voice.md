# Voice Routes

Endpoints for WebRTC voice/video signaling.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are prefixed with `/api/v1/` unless otherwise specified.

## Routes

- `GET /api/v1/voice/ice-servers`
- `GET /api/v1/voice/channels/{channel_id}/info`

## Purpose

- discover ICE server configuration that the client may use for WebRTC negotiation
- fetch voice-channel connection metadata for a specific channel

## SFU Backend

Plexichat uses mediasoup as the default SFU (Selective Forwarding Unit) backend. The mediasoup backend connects to an external mediasoup server via REST API, providing:

- **High performance**: Native C++ SFU with efficient media routing
- **Scalable**: Separate SFU process allows independent scaling
- **Production-ready**: Designed for real-world deployment loads

Alternative backends are available for specific use cases:
- `aiortc`: Pure Python WebRTC implementation that runs in-process -- ideal for development or lightweight deployments without a separate SFU
- `janus`: REST API adapter for Janus Gateway

Configure the SFU backend via the `voice.sfu_backend` config option (default: `mediasoup`). See [Voice Configuration](../config-voice.md) for details.

## Notes

- actual media transport is negotiated outside these REST endpoints
- the WebSocket gateway carries related voice signaling opcodes
- deployments can expose different ICE/TURN settings, so clients should treat the response as runtime data rather than static config

## Client Guidance

- request ICE servers from the backend instead of hardcoding them
- combine these routes with gateway voice events and voice opcodes
- fail gracefully when voice is disabled or unsupported on a given server

