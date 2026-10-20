# Email configuration

This guide describes how to configure email for Plexichat. Plexichat uses email for verification during registration, password reset, and notification delivery. If you set `authentication.accounts.require_email_verification` to `true` or you use password reset, you must configure SMTP. All defaults in this guide come from `src/config_defaults.py:626`.

## Before you begin

You configure email under the `email` key in `config.yaml`.

```yaml
email:
  smtp_host: "localhost"
  smtp_port: 587
  smtp_user: ""
  from_email: "noreply@plexichat.internal"
  use_tls: true
```

## Configure SMTP

```yaml
email:
  smtp_host: "localhost"
  smtp_port: 587
  smtp_user: ""
  from_email: "noreply@plexichat.internal"
  use_tls: true
```

| Field | Default | Description |
|---|---|---|
| `smtp_host` | `localhost` | Hostname of the SMTP server. Use `localhost` for a local mail server or the provider host such as `smtp.sendgrid.net`. |
| `smtp_port` | `587` | Port for SMTP. Use `587` for STARTTLS, `465` for implicit TLS, and avoid `25` in production. |
| `smtp_user` | `""` | Username or API key for SMTP authentication. Leave empty for local servers that do not require authentication. |
| `from_email` | `noreply@plexichat.internal` | Address in the `From` header. Use an address authorized by your provider. |
| `use_tls` | `true` | When `true`, the server uses TLS for SMTP. Set to `false` only for local development without TLS. |

Do not commit credentials to version control. Use environment variable substitution in `config.yaml`:

```yaml
email:
  smtp_user: "${SMTP_USER}"
  smtp_host: "${SMTP_HOST:-localhost}"
```

### SMTP host

*   For local delivery, keep `localhost` and run Postfix, Exim, or a similar mail server.
*   For external delivery, use your provider host such as `smtp.gmail.com` or `smtp.sendgrid.net`.
*   For reliable delivery at scale, consider a transactional service such as SendGrid, Mailgun, Amazon SES, or Postmark.

### SMTP port

*   Use `587` for SMTP with STARTTLS. This is the standard mail submission port.
*   Use `465` only if your provider requires SMTPS with implicit TLS.
*   Do not use `25` in production. It sends mail without encryption.

### SMTP user

Leave `smtp_user` empty for local servers. For external providers, set it to your username or API key and provide the password through an environment variable.

### From email

The `from_email` default `noreply@plexichat.internal` does not exist on public mail infrastructure. Change it to a real address on your domain, such as `noreply@mail.yourdomain.com`, and verify it with your provider.

### TLS

Keep `use_tls` set to `true` in production. If you set it to `false`, credentials travel in clear text.

## When you need email

You must configure email if you enable either of these features:

```yaml
authentication:
  accounts:
    require_email_verification: true
```

When `require_email_verification` is `true`, new users receive a verification email and must click the link before the server activates their account. Without working SMTP settings, users cannot complete registration. Password reset also requires email to deliver the reset link.

## Example: SendGrid

```yaml
email:
  smtp_host: "smtp.sendgrid.net"
  smtp_port: 587
  smtp_user: "apikey"
  from_email: "noreply@yourdomain.com"
  use_tls: true
```

Provide the API key through an environment variable:

```bash
export SMTP_PASSWORD="your_sendgrid_api_key"
```

## Example: Gmail

```yaml
email:
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  smtp_user: "your-address@gmail.com"
  from_email: "your-address@gmail.com"
  use_tls: true
```

Gmail requires an app password. Generate one in your Google Account under **Security > App passwords**.

## Related documentation

*   [Authentication configuration](config-authentication.md) — Email verification and registration settings.
*   [Default configuration reference](../../default-config.md) — Complete configuration reference.
