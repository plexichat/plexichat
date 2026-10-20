# Embeds and URL preview configuration

This guide describes how to configure embeds and URL previews for Plexichat. Embeds are rich cards with a title, description, images, and fields. URL preview fetches Open Graph metadata for links in messages. All defaults in this guide come from `src/config_defaults.py:801`.

## Before you begin

You configure embeds under the `embeds` key in `config.yaml`.

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

## Configure embed limits

```yaml
embeds:
  max_embeds_per_message: 10
  max_fields_per_embed: 25
  total_char_limit: 6000
```

| Field | Default | Description |
|---|---|---|
| `max_embeds_per_message` | `10` | Maximum embeds allowed in a single message. The server returns `400` if a bot or user exceeds this limit. |
| `max_fields_per_embed` | `25` | Maximum name/value field pairs per embed. |
| `total_char_limit` | `6000` | Combined character count across all embeds in a message, including title, description, field names, field values, footer text, and author name. |

For constrained clients, consider reducing `max_embeds_per_message` to `3` to lower rendering cost.

## Configure URL preview

When you enable URL preview, the server fetches metadata for URLs in messages and renders an inline preview card.

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

### Enable or disable URL preview

Set `enabled` to `false` if your server has limited outbound bandwidth or runs in a restricted network. When disabled, URLs remain clickable but no preview card appears.

### Timeout

`timeout_seconds` is `8` by default. It controls how long the server waits for a remote host. Increase it to `15` on slow networks or decrease it to `3` for faster response. Long timeouts delay message rendering while the preview loads.

### Maximum HTML size

`max_html_bytes` is `524288` (512 KB) by default. The server fetches only the first 512 KB of HTML. Lower it to `131072` (128 KB) for constrained environments.

### Maximum redirects

`max_redirects` is `5` by default. The server follows up to 5 HTTP redirects before it stops. Set it to `0` to disallow redirects or `3` for a tighter limit.

### Maximum image size

`max_image_size` is `5242880` (5 MB) by default. The server does not download preview images larger than this limit.

### Cache TTL

`cache_ttl_seconds` is `3600` (1 hour) by default. Increase it to `86400` (24 hours) to reduce re-fetching or decrease it to `1800` (30 minutes) for fresher previews.

### Rate limiting

`rate_limit_requests` is `10` and `rate_limit_window_seconds` is `60` by default. A user can trigger 10 URL fetches per 60 seconds. This limit is separate from the global API rate limit defined in `src/config_defaults.py:821`.

### Proxy images

`proxy_images` is `true` by default. When enabled, clients fetch preview images through your server instead of the original host. This protects user privacy and lets the server cache images. Disable it only if you have bandwidth constraints and you accept that clients connect directly to external hosts.

### Allowed schemes

`allowed_schemes` is `["http", "https"]` by default. Do not add schemes such as `ftp` or `file`. The scheme whitelist prevents server-side request forgery (SSRF) through non-HTTP protocols.

## Security considerations

URL preview requests originate from the server, not the client.

*   The `allowed_schemes` whitelist prevents SSRF through non-HTTP protocols.
*   The `max_redirects` limit prevents redirect loops.
*   The `timeout_seconds` and `max_html_bytes` limits prevent resource exhaustion.
*   If your server allows outbound access to internal networks, an attacker can probe internal hosts such as `http://10.0.0.5`. Apply egress firewall rules or disable URL preview in restricted networks.
*   URL preview requires outbound internet access. If your server cannot reach the internet, disable it.

## Related documentation

*   [Media configuration](config-media.md) — File upload and storage configuration.
*   [Rate limiting configuration](config-rate-limiting.md) — Global and per-user rate limits.
*   [Default configuration reference](../../default-config.md) — Complete configuration reference.
