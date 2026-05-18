# Voice Configuration

This guide covers voice and video configuration for deploying Plexichat in production. Voice settings control WebRTC signaling, STUN/TURN servers for NAT traversal, and SFU (Selective Forwarding Unit) backend selection. Proper configuration is essential for reliable voice/video connectivity across different network environments.

## Configuration Location

All voice settings are nested under the `voice` key in your configuration file:

```yaml
voice:
  # All voice settings go here
```

## SFU Backend Selection

Choose the Selective Forwarding Unit backend for handling voice/video streams.

### Configuration

```yaml
voice:
  enabled: true
  sfu_backend: "mediasoup"
```

**Note**: The default SFU backend is `mediasoup`, not `aiortc`. The `mediasoup` backend requires a separate mediasoup server (see below). For lightweight or development deployments without a separate SFU, use `aiortc`.

### Deployment Considerations

**Why SFU Backend Choice Matters**

The SFU backend determines how voice/video streams are routed between participants. This choice impacts scalability, performance, resource requirements, and operational complexity. See [Deployment Guide](deployment.md#scaling-considerations) for scaling strategies.

**aiortc (Python-based)**

**When to Use**

- Development and testing environments
- Small deployments with fewer than 50 concurrent voice users
- Single-server deployments
- Scenarios where operational simplicity is prioritized

**Advantages**

- No additional infrastructure required
- Built-in to Plexichat (Python-only)
- Simple configuration
- Lower operational complexity

**Limitations**

- Limited scalability (single-process)
- Higher CPU usage per participant
- No horizontal scaling capability
- Not suitable for large voice channels

**Performance Characteristics**

- CPU-intensive: Each participant's media is processed in Python
- Memory usage: Moderate (~50-100MB per participant)
- Network bandwidth: Standard WebRTC requirements
- Latency: Good (local processing)

**mediasoup (C++-based)**

**When to Use**

- Production deployments of any scale
- Multi-server deployments requiring horizontal scaling
- Large voice channels (50+ participants)
- High-performance requirements

**Advantages**

- Highly scalable (can handle hundreds of participants)
- Lower CPU usage per participant (C++ implementation)
- Horizontal scaling capability
- Advanced features (simulcast, SVC, transport-wide CC)

**Limitations**

- Requires separate mediasoup server installation
- Additional infrastructure and operational complexity
- Network latency between Plexichat and mediasoup
- Requires mediasoup configuration and monitoring

**mediasoup Variants**

- **mediasoup-ws**: WebSocket connection to mediasoup (recommended for new deployments)
- **mediasoup**: REST API connection to mediasoup
- Both require mediasoup server deployment

**Janus Gateway**

**When to Use**

- Existing Janus Gateway infrastructure
- Integration with other Janus-based services
- Deployments requiring Janus-specific features

**Advantages**

- Mature, battle-tested media server
- Extensive plugin ecosystem
- Supports multiple protocols (WebRTC, SIP, etc.)
- Good documentation and community support

**Limitations**

- Requires Janus Gateway installation and configuration
- Additional infrastructure complexity
- REST API latency
- Not specifically optimized for Plexichat use case

**Production Recommendation**

For production deployments, use **mediasoup** for best scalability and performance. For development or small deployments, **aiortc** provides the simplest setup.

---

## aiortc Configuration

Settings for the built-in Python-based SFU.

### Configuration

```yaml
voice:
  enabled: true
  sfu_backend: "mediasoup"
```

**Note**: The default SFU backend is `mediasoup`, not `aiortc`. The `mediasoup` backend requires a separate mediasoup server (see below). For lightweight or development deployments without a separate SFU, use `aiortc`.

### Deployment Considerations

**Resource Requirements**

- **CPU**: ~10-20% CPU core per participant (varies by codec and quality)
- **Memory**: ~50-100MB RAM per participant
- **Network**: Standard WebRTC bandwidth (up to 2Mbps per participant for HD video)
- **Ports**: UDP port range for media transport (typically 50000-60000)

**Performance Tuning**

- **Codec Selection**: Opus for audio, VP8/VP9/H.264 for video
- **Bitrate**: Configure appropriate bitrate based on network conditions
- **Simulcast**: Not available in aiortc (consider mediasoup for this feature)
- **CPU Pinning**: Consider pinning Python process to specific CPU cores

**Operational Notes**

- Monitor CPU usage during peak voice usage
- Ensure sufficient network bandwidth for media traffic
- Configure firewall to allow UDP media ports
- Test with expected number of participants before production

---

## mediasoup Configuration

Settings for mediasoup-based SFU deployment.

### Configuration

```yaml
voice:
  enabled: true
  sfu_backend: "mediasoup"
  mediasoup_url: "https://localhost:4443"
```

**Note**: The SFU backend value is `mediasoup`, not `mediasoup-ws`. The `mediasoup_origin` key exists in the signaling manager code but is not exposed as a top-level config key -- it defaults to `https://localhost`. The `mediasoup_url` uses `https://` (REST API) or `wss://` (WebSocket) depending on your mediasoup server setup.

### Deployment Considerations

**mediasoup Installation**

1. Install Node.js (required for mediasoup)
2. Install mediasoup:
   ```bash
   npm install mediasoup
   ```

3. Deploy mediasoup server (see mediasoup documentation for server setup)
4. Configure WebSocket or REST API endpoint

**WebSocket vs REST API**

- **mediasoup-ws**: WebSocket connection (recommended for real-time bidirectional communication)
- **mediasoup**: REST API connection (simpler firewall rules, higher latency)

**URL Configuration**

- **mediasoup_url**: WebSocket URL (wss://) or REST API URL (https://)
- **mediasoup_origin**: Origin header for CORS (required for WebSocket connections)
- **SSL/TLS**: Use wss:// and https:// for production security

**Resource Requirements**

- **CPU**: ~2-5% CPU core per participant (much more efficient than aiortc)
- **Memory**: ~10-20MB RAM per participant
- **Network**: Standard WebRTC bandwidth
- **Scaling**: Can scale horizontally across multiple mediasoup instances

**Operational Notes**

- Deploy mediasoup on separate server from Plexichat for better resource isolation
- Use load balancer for multiple mediasoup instances
- Monitor mediasoup health and performance metrics
- Implement health checks and automatic failover
- Configure appropriate codec settings in mediasoup

---

## Janus Configuration

Settings for Janus Gateway integration.

### Configuration

```yaml
voice:
  enabled: true
  sfu_backend: "janus"
  janus_url: "http://localhost:8088/janus"
```

### Deployment Considerations

**Janus Installation**

1. Install dependencies (libnice, openssl, libsrtp, etc.)
2. Install Janus Gateway:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install janus

   # Or build from source
   git clone https://github.com/meetecho/janus-gateway.git
   cd janus-gateway
   sh autogen.sh
   ./configure --enable-docs --prefix=/opt/janus
   make
   sudo make install
   ```

3. Configure Janus (edit `/etc/janus/janus.jcfg` or `/opt/janus/etc/janus/janus.jcfg`)
4. Start Janus Gateway

**URL Configuration**

- **janus_url**: REST API URL for Janus Gateway
- **SSL/TLS**: Use https:// for production security
- **Authentication**: Configure Janus authentication if required

**Resource Requirements**

- **CPU**: ~5-10% CPU core per participant
- **Memory**: ~20-40MB RAM per participant
- **Network**: Standard WebRTC bandwidth
- **Plugins**: Ensure video room plugin is enabled

**Operational Notes**

- Configure Janus for production use (security, logging, etc.)
- Monitor Janus health and performance
- Use Janus admin API for monitoring and management
- Configure appropriate codec settings in Janus configuration

---

## STUN/TURN Configuration

Configure STUN and TURN servers for NAT traversal.

### Configuration

```yaml
voice:
  stun_urls:
    - "stun:stun.l.google.com:19302"
  turn_urls: []
  turn_secret: ""
  turn_ttl: 86400
  turn_username: ""
  turn_credential: ""
```

### Deployment Considerations

**Why STUN/TURN Matters**

WebRTC uses peer-to-peer connections that often fail due to NAT (Network Address Translation). STUN helps discover public IP addresses. TURN relays traffic when direct connection is impossible.

**STUN Servers**

- **Purpose**: Help peers discover their public IP address and port
- **Cost**: Free public STUN servers available (Google, Twilio, etc.)
- **Reliability**: Public STUN servers are generally reliable
- **Required**: Always configure at least one STUN server

**Public STUN Servers**

- Google: `stun:stun.l.google.com:19302`
- Twilio: `stun:global.stun.twilio.com:3478`
- Mozilla: `stun:stun.services.mozilla.com`
- Multiple STUN servers improve reliability

**TURN Servers**

- **Purpose**: Relay media traffic when direct connection fails (symmetric NAT, firewalls)
- **Cost**: Requires bandwidth and hosting (not free)
- **Required**: Essential for connectivity behind restrictive NAT/firewalls
- **Deployment**: Can use coturn (open source) or commercial TURN services

**When TURN is Required**

- Users behind symmetric NAT (common in corporate networks)
- Users behind restrictive firewalls - see [Deployment Guide](deployment.md#network-configuration)
- Mobile networks (carrier-grade NAT)
- Any scenario where direct P2P connection fails

**TURN Deployment Options**

**Self-Hosted (coturn)**

1. Install coturn:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install coturn

   # Or build from source
   git clone https://github.com/coturn/coturn.git
   cd coturn
   ./configure
   make
   sudo make install
   ```

2. Configure coturn (`/etc/turnserver.conf`):
   ```
   listening-port=3478
   external-ip=<your-public-ip>
   realm=plexichat.example
   user=plexichat:<shared-secret>
   lt-cred-mech
   ```

3. Start coturn:
   ```bash
   sudo systemctl start coturn
   sudo systemctl enable coturn
   ```

**Commercial TURN Services**

- Twilio Network Traversal
- Xirsys
- Metered.ca
- These services provide TURN servers with monthly pricing

**TURN Authentication Methods**

**Time-Limited Credentials (Recommended)**

- Use `turn_secret` and `turn_ttl` for time-limited credentials
- More secure than static credentials
- Credentials expire after TTL (86400 seconds = 24 hours default)
- Requires coturn or TURN service that supports time-limited credentials

**Static Credentials**

- Use `turn_username` and `turn_credential` for static credentials
- Simpler but less secure
- Never rotate credentials
- Suitable for services like Metered.ca that use static credentials

**TURN Configuration Example**

```yaml
voice:
  stun_urls:
    - "stun:stun.l.google.com:19302"
    - "stun:stun1.l.google.com:19302"
  turn_urls:
    - "turn:turn.example.com:3478?transport=udp"
    - "turn:turn.example.com:3478?transport=tcp"
  turn_secret: "your-secret-key-here"
  turn_ttl: 86400
```

**Operational Notes**

- Test TURN connectivity from various network environments
- Monitor TURN server bandwidth usage and costs
- Configure firewall to allow TURN traffic (UDP 3478, TCP 443 for TURN over TLS)
- Use multiple TURN servers for redundancy
- Monitor TURN server health and availability

---

## Bitrate Configuration

Configure maximum bitrate for voice/video streams.

### Configuration

```yaml
# Bitrate is not a server config key. It is controlled by the SFU backend
# (mediasoup/janus) and WebRTC negotiation. Configure bitrate limits in your
# SFU server configuration, not in the Plexichat config file.
```

**Note**: There is no `max_bitrate` config key in the Plexichat server. Bitrate is negotiated between clients and the SFU backend. To enforce bitrate limits, configure them in your mediasoup or Janus server settings.

### Deployment Considerations

**Why Bitrate Matters**

Bitrate determines audio/video quality and bandwidth consumption. Higher bitrate provides better quality but requires more bandwidth and can cause performance issues on slow connections.

**Bitrate Recommendations**

- **Voice Only**: 64-128 kbps (128000 = 128 kbps)
- **Standard Video**: 500-1000 kbps (500000-1000000)
- **HD Video**: 1500-3000 kbps (1500000-3000000)
- **4K Video**: 8000-15000 kbps (8000000-15000000)

**Default Value**

- **Default**: 128000 (128 kbps) is appropriate for voice-only or low-quality video
- **Production**: Increase to 500000 (500 kbps) for standard video quality
- **High-Quality**: Increase to 2000000 (2 Mbps) for HD video
- **Bandwidth-Constrained**: Reduce to 64000 (64 kbps) for voice-only

**Adaptive Bitrate**

- Plexichat may implement adaptive bitrate based on network conditions
- Configure max_bitrate as the upper limit
- Lower quality automatically on poor connections

**Operational Notes**

- Monitor bandwidth usage during voice/video calls
- Adjust based on user network conditions and feedback
- Consider implementing quality selection in client application
- Test with various network conditions before production

---

## Participant Limits

Configure maximum participants per voice channel.

### Configuration

```yaml
# There is no max_participants_per_channel config key in the Plexichat server.
# Participant limits are enforced by the SFU backend and user feature tiers.
# See the user_features.rate_limit_tiers section in default-config.md for
# max_voice_minutes_per_day limits per tier.
```

**Note**: There is no `max_participants_per_channel` config key. Participant limits per voice channel are handled by the SFU backend's configuration, not by the Plexichat config file. See your mediasoup or Janus documentation for room size limits.

### Deployment Considerations

**Why Participant Limits Matter**

Participant limits prevent resource exhaustion, ensure quality of service, and manage infrastructure costs. Without limits, large voice channels could overwhelm server resources.

**Participant Limit Guidelines**

- **aiortc Backend**: 10-20 participants (CPU-intensive)
- **mediasoup Backend**: 50-100 participants (scalable)
- **Janus Backend**: 50-100 participants (scalable)
- **Default**: 25 is a conservative limit for most backends

**Resource Impact**

- **CPU**: Each participant adds processing load
- **Memory**: Each participant consumes memory for media buffers
- **Network**: Each participant sends/receives media streams
- **Bandwidth**: N*(N-1) connections in mesh, N connections in SFU

**SFU vs Mesh**

- **SFU (Selective Forwarding Unit)**: Each participant sends 1 stream, receives N streams. Server handles routing. Scales better.
- **Mesh**: Each participant connects to every other participant. N*(N-1) total connections. Doesn't scale well.

Plexichat uses SFU architecture, so participant count scales linearly with server resources.

**Operational Notes**

- Monitor CPU and memory usage during large voice channels
- Implement participant limits per server instance
- Consider sharding large channels across multiple SFU instances
- Provide user feedback when channel is full
- Monitor voice channel sizes and adjust limits based on usage patterns

---

## Network Configuration

Configure network settings for voice/video traffic.

### Firewall Configuration

**Required Ports**

- **STUN/TURN**: UDP 3478, TCP 3478
- **Media Transport**: UDP 50000-60000 (configurable in SFU)
- **WebSocket**: TCP 4443 (for mediasoup-ws)
- **REST API**: TCP 8088 (for Janus)

**Firewall Rules**

```bash
# Allow STUN/TURN
sudo ufw allow 3478/udp
sudo ufw allow 3478/tcp

# Allow media transport (adjust range as needed)
sudo ufw allow 50000:60000/udp

# Allow mediasoup WebSocket
sudo ufw allow 4443/tcp

# Allow Janus REST API
sudo ufw allow 8088/tcp
```

**NAT Traversal**

- Ensure your server has a public IP address or proper port forwarding
- Configure static port mapping if behind NAT
- Test connectivity from external networks
- Consider using TURN for users behind restrictive NAT

---

## Scaling Considerations

### Vertical Scaling (Single Server)

**When to Use**

- Deployments with fewer than 100 concurrent voice users
- Simpler operational requirements
- Limited budget for multiple servers

**Configuration Tips**

- Use mediasoup for better resource efficiency
- Allocate sufficient CPU and memory for voice processing
- Monitor resource usage during peak voice usage
- Consider CPU pinning for better performance

### Horizontal Scaling (Multiple SFU Instances)

**When to Use**

- Deployments with more than 100 concurrent voice users
- High availability requirements
- Geographic distribution

**Configuration Tips**

- Use mediasoup for horizontal scaling capability
- Deploy load balancer for SFU instances
- Implement session affinity or routing logic
- Monitor SFU instance health and load
- Consider geographic distribution for low latency

### Geographic Distribution

**When to Use**

- Global user base
- Low latency requirements
- Regional voice channels

**Configuration Tips**

- Deploy SFU instances in multiple regions
- Route users to nearest SFU based on geolocation
- Use TURN servers in each region
- Monitor inter-region latency and quality
- Consider edge computing (Cloudflare Workers, etc.)

---

## Monitoring and Troubleshooting

### Key Metrics to Monitor

- **Active Voice Channels**: Number of ongoing voice/video calls
- **Total Participants**: Total users in voice channels
- **CPU Usage**: SFU CPU utilization
- **Memory Usage**: SFU memory consumption
- **Network Bandwidth**: Media traffic bandwidth
- **Packet Loss**: Percentage of lost packets (quality indicator)
- **Latency**: Round-trip time for media packets
- **TURN Usage**: Number of users using TURN relay

### Common Issues

**Audio/Video Not Working**

- Check STUN/TURN server connectivity
- Verify firewall port configuration
- Test from different network environments
- Check browser console for WebRTC errors

**Poor Quality**

- Check network bandwidth and latency
- Verify bitrate configuration
- Check CPU usage on SFU server
- Test with lower bitrate settings

**Connection Drops**

- Check network stability
- Verify TURN server reliability
- Check SFU server health
- Monitor for resource exhaustion

**High CPU Usage**

- Check participant count per channel
- Verify codec settings
- Consider using mediasoup for better efficiency
- Scale horizontally if needed

### Health Checks

```bash
# Test STUN server
stunclient stun.l.google.com:19302

# Test TURN server
turnclient --server turn.example.com:3478 --username plexichat --password secret

# Check mediasoup health
curl https://mediasoup.example.com/health

# Check Janus health
curl http://localhost:8088/janus/info
```

---

## Related Documentation

- [Default Configuration Reference](../../default-config.md) - Complete configuration reference
- [Deployment Guide](../getting-started.md) - Network configuration and scaling
- [Security Best Practices](../../security.md) - Voice security considerations
- [Media Configuration](config-media.md) - Related media processing settings
