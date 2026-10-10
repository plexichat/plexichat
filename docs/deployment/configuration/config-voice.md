# Voice configuration

This guide describes how to configure voice and video for Plexichat. Voice settings control WebRTC signaling, STUN and TURN servers for NAT traversal, and the SFU (Selective Forwarding Unit) backend. Correct configuration is essential for reliable connectivity.

## Configuration location

Add voice settings to your `config.yaml` file under the `voice` key (`src/config_defaults.py:397`):

```yaml
voice:
  # Voice settings
```

## SFU backend selection

Choose the SFU backend that routes voice and video streams between participants.

### Configuration

```yaml
voice:
  enabled: true
  sfu_backend: "aiortc"   # Default is aiortc. Defined in src/config_defaults.py:399
```

Plexichat supports four backend values in `src/core/voice/signaling/sfu/__init__.py:45` and `src/core/voice/signaling/manager/base.py:32`:

| Value | Implementation | Transport |
| --- | --- | --- |
| `aiortc` | Pure Python in `src/core/voice/signaling/sfu/aiortc_adapter.py` | In-process |
| `mediasoup-ws` | WebSocket adapter in `src/core/voice/signaling/sfu/mediasoup_ws.py` | `wss://` or `ws://` |
| `mediasoup` | REST adapter in `src/core/voice/signaling/sfu/mediasoup.py` | `https://` or `http://` |
| `janus` | REST adapter in `src/core/voice/signaling/sfu/janus.py` | `http://` or `https://` |

> **Note:** The fallback in `src/core/voice/signaling/manager/base.py:32` is `aiortc` when `sfu_backend` is absent. Use `mediasoup-ws` when your mediasoup server exposes the `mediasoup-demo` WebSocket API.

### Deployment considerations

**aiortc (Python-based, default)**

Use aiortc when you want operational simplicity.

*   **When to use it:** Development, testing, or single-server deployments with fewer than 50 concurrent voice users.
*   **Advantages:** No external SFU is required. The SFU runs inside the Plexichat process. Configuration is minimal.
*   **Limitations:** Single-process and CPU-intensive. Each participant uses about 10–20% of a CPU core and 50–100 MB of RAM. Not suitable for large channels.
*   **Performance:** Good latency for local processing. No horizontal scaling.

**mediasoup (C++-based)**

Use mediasoup for production and large channels.

*   **When to use it:** Production deployments, large channels with 50 or more participants, or when you need horizontal scaling.
*   **Advantages:** Highly scalable, lower CPU per participant (C++), simulcast and SVC support, transport-wide congestion control.
*   **Limitations:** Requires a separate Node.js mediasoup server and additional monitoring.

**Janus Gateway**

Use Janus when you already run Janus Gateway or need its plugin ecosystem.

*   **When to use it:** Existing Janus infrastructure or deployments that need SIP interop.
*   **Advantages:** Mature media server with extensive plugins.
*   **Limitations:** Requires Janus installation and configuration. Not optimized for Plexichat.

**Recommendation:** For production, use `mediasoup` or `mediasoup-ws`. For development or small single-server deployments, use `aiortc`.

## aiortc configuration

The built-in Python SFU requires no external service.

### Configuration

```yaml
voice:
  enabled: true
  sfu_backend: "aiortc"
```

The adapter is `src/core/voice/signaling/sfu/aiortc_adapter.py:110`. It raises `SFUConnectionError` at startup if the `aiortc` package is not installed.

Optional settings forwarded to the adapter:

```yaml
voice:
  stun_urls:
    - "stun:stun.l.google.com:19302"
  turn_urls: []
  turn_username: ""
  turn_credential: ""
  turn_secret: ""
  turn_ttl: 86400
  acoustic_defense:
    enabled: false
```

In `src/core/voice/signaling/manager/base.py:72`, Plexichat converts `stun_urls` and `turn_urls` to the `ice_servers` list that `AiortcAdapter` expects.

### Deployment considerations

*   Install `aiortc==1.15.0` (`requirements.in:62`). Also install native dependencies for Opus, libsrtp, and libvpx.
*   Reserve UDP ports for media if your firewall restricts outbound traffic.
*   Monitor CPU during peak voice usage. Move to mediasoup if CPU saturates.

## mediasoup configuration

### Configuration

```yaml
voice:
  enabled: true
  sfu_backend: "mediasoup"        # Or mediasoup-ws for WebSocket mode
  mediasoup_url: "https://localhost:4443"
```

*   `mediasoup_url` is defined in `src/config_defaults.py:400`.
*   The manager in `src/core/voice/signaling/manager/base.py:95` converts `https://` to `wss://` and `http://` to `ws://` when `sfu_backend` is `mediasoup-ws`.
*   The internal config also includes `mediasoup_origin` (default `https://localhost` in `src/core/voice/signaling/manager/base.py:34`), which is sent as the `Origin` header for CORS on WebSocket connections. It is not a top-level key in `config.yaml`.

### Deployment considerations

1.  Install Node.js and mediasoup:

    ```bash
    npm install mediasoup
    ```

2.  Deploy the mediasoup server and expose either a WebSocket endpoint (`mediasoup-ws`) or a REST endpoint (`mediasoup`).
3.  Point `mediasoup_url` to that endpoint. Use `wss://` or `https://` in production.

**WebSocket versus REST**

*   `mediasoup-ws` — Bidirectional WebSocket (recommended for real-time signaling).
*   `mediasoup` — REST API (simpler firewall rules, higher latency).

**Resource requirements**

*   About 2–5% of a CPU core and 10–20 MB of RAM per participant.
*   Scales horizontally. Put multiple mediasoup hosts behind a load balancer.

## Janus configuration

### Configuration

```yaml
voice:
  enabled: true
  sfu_backend: "janus"
  janus_url: "http://localhost:8088/janus"   # Defined in src/config_defaults.py:401
```

### Deployment considerations

1.  Install Janus Gateway and its dependencies (`libnice`, `openssl`, `libsrtp`).
2.  Enable the VideoRoom plugin.
3.  Start Janus and set `janus_url` to the REST API. Use `https://` in production.

## STUN and TURN configuration

Configure STUN and TURN servers for NAT traversal.

### Configuration

```yaml
voice:
  stun_urls:
    - "stun:stun.l.google.com:19302"
    - "stun:stun1.l.google.com:19302"
    - "stun:stun2.l.google.com:19302"
    - "stun:stun3.l.google.com:19302"
  turn_urls: []
  turn_secret: ""
  turn_ttl: 86400
  turn_username: ""
  turn_credential: ""
```

Defaults are in `src/config_defaults.py:402`. The ICE server builder `src/core/voice/signaling/turn.py` (via `src/core/voice/signaling/manager/base.py:119`) turns these values into the ICE servers that Plexichat returns to clients.

### Deployment considerations

**Why STUN and TURN matter**

WebRTC uses peer-to-peer connections. NAT blocks many direct connections. STUN helps peers discover their public IP address. TURN relays media when a direct connection is not possible.

**STUN servers**

*   Always configure at least one STUN server.
*   Free public servers include `stun:stun.l.google.com:19302`, `stun:global.stun.twilio.com:3478`, and `stun:stun.services.mozilla.com`.
*   Configure multiple servers for redundancy.

**TURN servers**

You need TURN when users are behind symmetric NAT, restrictive firewalls, or carrier-grade NAT (mobile networks).

Options:

*   Self-host `coturn` (open source)
*   Commercial services such as Twilio Network Traversal, Xirsys, or Metered.ca

**TURN authentication**

*   **Time-limited credentials (recommended):** Set `turn_secret` and `turn_ttl`. Coturn generates temporary credentials. The default TTL is `86400` seconds (24 hours).
*   **Static credentials:** Set `turn_username` and `turn_credential`. Use this for services that issue static credentials.

**Example with TURN**

```yaml
voice:
  stun_urls:
    - "stun:stun.l.google.com:19302"
    - "stun:stun1.l.google.com:19302"
  turn_urls:
    - "turn:turn.example.com:3478?transport=udp"
    - "turn:turn.example.com:3478?transport=tcp"
  turn_secret: "your-secret-key"
  turn_ttl: 86400
```

The signaling manager in `src/core/voice/signaling/manager/base.py:76` includes TURN URLs with static credentials when `turn_username` and `turn_credential` are both set, and includes bare TURN URLs when `turn_secret` is set.

## Acoustic defense

Plexichat can apply optional acoustic eavesdropping defenses to audio tracks.

```yaml
voice:
  acoustic_defense:
    enabled: false
    jitter_ms_min: 5.0
    jitter_ms_max: 20.0
    spectral_masking: true
    spectral_mask_noise_db: -40.0
    spectral_mask_low_hz: 1000.0
    spectral_mask_high_hz: 8000.0
    vad_gating: true
    vad_speech_threshold: 0.02
    vad_silence_frames: 3
    transient_shaving: true
    transient_attack_ms: 0.5
    transient_release_ms: 5.0
    transient_ratio: 0.3
```

The defaults are in `src/config_defaults.py:414` and the dataclass is in `src/core/voice/signaling/sfu/base.py:27`. When `enabled` is `true`, the SFU wraps audio tracks with `src/core/voice/signaling/sfu/acoustic_defense.py:AudioProcessingTrack`.

## Bitrate and participant limits

### Configuration

```yaml
# Plexichat has no max_bitrate or max_participants_per_channel keys.
# Configure bitrate and room size in your SFU server, not in config.yaml.
# Participant limits per tier are in user_features.rate_limit_tiers
# (src/config_defaults.py:345).
```

Bitrate is negotiated between clients and the SFU. To enforce a cap, configure the limit in your `mediasoup` or `Janus` server.

For limits per tier, see `src/config_defaults.py:345`. For example, the `standard` tier allows `max_voice_minutes_per_day: 120` and the `premium` tier allows unlimited minutes. These values control how long a user can stay in voice, not how many users can join a channel.

### Deployment considerations

**Bitrate guidelines**

*   Voice only: 64–128 kbps
*   Standard video: 500–1000 kbps
*   HD video: 1500–3000 kbps

**Participant guidelines**

*   aiortc: 10–20 participants per channel
*   mediasoup or Janus: 50–100 participants per channel

Plexichat uses an SFU architecture. Each participant sends one stream to the server and receives N streams. This scales linearly, unlike a mesh.

## Network configuration

Open the ports that your SFU and TURN deployment needs:

| Service | Ports |
| --- | --- |
| STUN and TURN | UDP and TCP `3478` |
| TURN over TLS | TCP `443` |
| Media transport (aiortc) | UDP `50000`–`60000` (configurable) |
| mediasoup WebSocket | TCP `4443` |
| Janus REST API | TCP `8088` |

Example firewall rules:

```bash
sudo ufw allow 3478/udp
sudo ufw allow 3478/tcp
sudo ufw allow 50000:60000/udp
sudo ufw allow 4443/tcp
sudo ufw allow 8088/tcp
```

Ensure the Plexichat host has a public IP or correct port forwarding so external clients can reach it.

## Scaling

**Vertical scaling (single server)**

Use vertical scaling for fewer than 100 concurrent voice users. Prefer mediasoup over aiortc for better CPU efficiency. Monitor CPU and memory during peak usage.

**Horizontal scaling (multiple SFU instances)**

Use horizontal scaling for more than 100 concurrent voice users. Deploy mediasoup on separate hosts, add a load balancer, and monitor instance health. aiortc does not support horizontal scaling.

**Geographic distribution**

For a global user base, deploy SFU and TURN servers in multiple regions and route users to the nearest region.

## Monitoring and troubleshooting

**Metrics to monitor**

*   Active voice channels and total participants
*   SFU CPU and memory usage
*   Network bandwidth and packet loss
*   TURN relay usage

**Common issues**

*   **Audio or video fails:** Check STUN and TURN connectivity and firewall ports. Look for WebRTC errors in the browser console.
*   **Poor quality:** Check bandwidth, bitrate caps on the SFU, and SFU CPU usage.
*   **High CPU:** Reduce participants per channel or switch from aiortc to mediasoup.

**Health checks**

```bash
# Test STUN
stunclient stun.l.google.com:19302

# Test TURN
turnutils_uclient -p 3478 -W <turn_secret> turn.example.com

# Check mediasoup
curl https://mediasoup.example.com/health

# Check Janus
curl http://localhost:8088/janus/info
```

## Related documentation

*   [Default configuration reference](../../default-config.md)
*   [Deployment guide](../getting-started.md)
*   [Security best practices](../../security.md)
*   [Media configuration](config-media.md)
