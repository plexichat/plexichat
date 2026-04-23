# Email Configuration

This guide covers email configuration for Plexichat. Email is used for email verification during registration, password reset flows, and notification delivery. If you enable `require_email_verification` or need password reset functionality, you must configure SMTP settings.

## Configuration Location

All email settings are nested under the `email` key in your configuration file:

```yaml
email:
  smtp_host: "localhost"
  smtp_port: 587
  smtp_user: ""
  from_email: "noreply@plexichat.internal"
  use_tls: true
```

## SMTP Settings

### Configuration

```yaml
email:
  smtp_host: "localhost"
  smtp_port: 587
  smtp_user: ""
  from_email: "noreply@plexichat.internal"
  use_tls: true
```

### Deployment Considerations

**SMTP Host**

- **Default**: `localhost` -- assumes a local mail server (Postfix, Exim, etc.)
- **External SMTP**: Use your email provider's SMTP server (e.g., `smtp.gmail.com`, `smtp.sendgrid.net`)
- **Transaction Email Services**: Consider SendGrid, Mailgun, Amazon SES, or Postmark for reliable delivery

**SMTP Port**

- **587** (default): SMTP with STARTTLS -- the standard port for mail submission
- **465**: SMTPS (implicit TLS) -- use if your provider requires it
- **25**: Unencrypted SMTP -- never use in production

**SMTP User**

- Leave empty for local mail servers that do not require authentication
- Set to your provider's username/API key for external SMTP services
- Never commit credentials to version control; use environment variables:

```yaml
email:
  smtp_user: "${SMTP_USER}"
```

**From Email**

- The address that appears in the "From" header of outgoing emails
- **Default**: `noreply@plexichat.internal` -- change this to a real address on your domain
- Must be an address authorized by your SMTP provider (especially for transactional email services)
- Consider using a dedicated subdomain like `noreply@mail.yourdomain.com`

**Use TLS**

- **Default**: `true` -- always use TLS in production
- Set to `false` only for local development with a non-TLS mail server
- Never disable TLS when sending credentials over the network

## When Email Is Required

Email must be configured if you enable any of these features:

```yaml
authentication:
  accounts:
    require_email_verification: true
```

When `require_email_verification: true`, new registrations receive a verification email and must click the link before their account is activated. Without a working SMTP configuration, users will be unable to complete registration.

Password reset functionality also requires email to send the reset link.

## Example: SendGrid Configuration

```yaml
email:
  smtp_host: "smtp.sendgrid.net"
  smtp_port: 587
  smtp_user: "apikey"
  from_email: "noreply@yourdomain.com"
  use_tls: true
```

Set the SMTP password via environment variable:

```bash
export SMTP_PASSWORD="your_sendgrid_api_key"
```

## Example: Gmail Configuration

```yaml
email:
  smtp_host: "smtp.gmail.com"
  smtp_port: 587
  smtp_user: "your-address@gmail.com"
  from_email: "your-address@gmail.com"
  use_tls: true
```

**Note**: Gmail requires an "App Password" for SMTP access. Generate one in your Google Account settings under Security > App passwords.

## Related Documentation

- [Authentication Configuration](config-authentication.md) -- email verification and registration settings
- [Default Configuration Reference](../../default-config.md) -- complete config reference
