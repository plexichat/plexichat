# Voice Routes

The REST voice routes provide signaling metadata rather than media transport.

## Routes

- `GET /voice/ice-servers`
- `GET /voice/channels/{channel_id}/info`

## Purpose

- discover ICE server configuration that the client may use for WebRTC negotiation
- fetch voice-channel connection metadata for a specific channel

## Notes

- actual media transport is negotiated outside these REST endpoints
- the WebSocket gateway carries related voice signaling opcodes
- deployments can expose different ICE/TURN settings, so clients should treat the response as runtime data rather than static config

## Client Guidance

- request ICE servers from the backend instead of hardcoding them
- combine these routes with gateway voice events and voice opcodes
- fail gracefully when voice is disabled or unsupported on a given server

