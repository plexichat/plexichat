# Embeds & URL Preview Configuration

This guide covers embed and URL preview configuration for Plexichat. Embeds allow rich content in messages (cards with title, description, images, fields). URL preview automatically fetches and displays link previews when users post URLs.

## Configuration Location

All embed settings are nested under the `embeds` key in your configuration file:

```yaml
embeds:
  max_embeds_per_message: 10
  max_fields_per_embed: 25
  total_char_limit: 6000
  url_preview:
    enabled: true
    timeout_seconds: 8
    max_html_bytes: 524288
    max_redirects: 5
    max_image_size: 5242880
    cache_ttl_seconds: 3600
    rate_limit_requests: 10
    rate_limit_window_seconds: 60
    proxy_images: true
    allowed_schemes: ["http", "https"]
```

## Embed Limits

### Configuration

```yaml
embeds:
  max_embeds_per_message: 10
  max_fields_per_embed: 25
  total_char_limit: 6000
```

### Deployment Considerations

**Max Embeds Per Message**
- Default: 10 -- allows bots and integrations to attach multiple embeds per message
- Lower this (e.g., 3) to reduce message size and rendering cost in the client
- Bots that send more embeds than the limit will receive a 400 validation error

**Max Fields Per Embed**
- Default: 25 -- each embed can have up to 25 name/value field pairs
- This matches common chat platform conventions

**Total Character Limit**
- Default: 6000 -- combined character count across all embeds in a single message
- Includes title, description, field names, field values, footer text, and author name
- Messages exceeding this limit are rejected with a 400 error

## URL Preview

When enabled, Plexichat fetches Open Graph and other metadata from URLs posted in messages and displays them as inline previews.

### Configuration

```yaml
embeds:
  url_preview:
    enabled: true
    timeout_seconds: 8
    max_html_bytes: 524288
    max_redirects: 5
    max_image_size: 5242880
    cache_ttl_seconds: 3600
    rate_limit_requests: 10
    rate_limit_window_seconds: 60
    proxy_images: true
    allowed_schemes: ["http", "https"]
```

### Deployment Considerations

**Enabling URL Preview**
- Default: enabled -- URL previews are on by default
- Disable (`enabled: false`) if your server has limited outbound bandwidth or runs in a restricted network
- When disabled, URLs are still clickable but no preview card is generated

**Timeout**
- Default: 8 seconds -- maximum time to wait for a remote server to respond
- Increase for slow networks (e.g., 15 seconds), decrease for faster response (e.g., 3 seconds)
- Long timeouts delay message rendering in the client while the preview loads

**Max HTML Bytes**
- Default: 524288 (512KB) -- only the first 512KB of HTML is fetched and parsed
- This prevents downloading enormous pages that would waste bandwidth and memory
- Lower for constrained environments (e.g., 131072 for 128KB)

**Max Redirects**
- Default: 5 -- follows up to 5 HTTP redirects before giving up
- Set to 0 to disable redirect following entirely
- Set to 3 for a tighter redirect limit

**Max Image Size**
- Default: 5242880 (5MB) -- preview images larger than this are not downloaded
- The server fetches preview images to proxy them to the client (when `proxy_images` is true)

**Cache TTL**
- Default: 3600 seconds (1 hour) -- preview data is cached server-side
- Increase for less frequent re-fetching (e.g., 86400 for 24 hours)
- Decrease for fresher previews (e.g., 1800 for 30 minutes)

**Rate Limiting**
- Default: 10 requests per 60 seconds per user -- prevents users from triggering excessive URL fetches
- This is separate from the global API rate limit
- Increase for busy servers where users share many links

**Proxy Images**
- Default: true -- preview images are proxied through the Plexichat server
- When enabled, the client fetches preview images from your server rather than the original URL
- This protects user privacy (no direct connection to external servers) and allows caching
- Disable only if you have bandwidth constraints; clients will load images directly from source URLs

**Allowed Schemes**
- Default: `["http", "https"]` -- only HTTP/HTTPS URLs are fetched for previews
- Do not add other schemes (e.g., `ftp`, `file`) as this poses security risks
- Internal network URLs (e.g., `http://10.0.0.5`) will be fetched if accessible from the server

## Security Considerations

- URL preview fetches are made from the server, not the client -- the server acts as a proxy
- The `allowed_schemes` whitelist prevents SSRF via non-HTTP protocols
- The `max_redirects` limit prevents redirect loops
- The `timeout_seconds` and `max_html_bytes` limits prevent resource exhaustion
- Consider network egress restrictions: URL preview requires outbound internet access from the server
- If your server runs in a network with restricted egress, disable URL preview or whitelist specific domains in your firewall

## Related Documentation

- [Media Configuration](deployment/configuration/config-media.md) -- file upload and storage configuration
- [Authentication Configuration](deployment/configuration/config-authentication.md) -- message encryption and limits
- [Rate Limiting Configuration](deployment/configuration/config-rate-limiting.md) -- global and per-user rate limits
