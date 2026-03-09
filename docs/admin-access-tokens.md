# API Access Tokens

Some Plexichat servers can require an additional access token for authenticated REST API requests.

## When This Applies

If access-token gating is enabled, an authenticated request needs both:

- a normal user or bot authorization header
- an `X-API-Access-Token` header

You can detect this requirement with `GET {{BASE_URL}}/capabilities` by checking `access_token_required`.

## Request Shape

```http
Authorization: Bearer <session-token>
X-API-Access-Token: <access-token>
```

## What The Gate Is For

This mechanism is useful for closed deployments, staged testing, or intentionally restricted API exposure.

## Client Behavior

- treat the access token as sensitive credential material
- do not hardcode it into public clients
- surface a clear error when a server requires it but none is configured
- support rotation, expiration, or revocation without requiring code changes

## Scope Of This Page

This page describes the request contract only. Administrative token issuance, private scope rules, and operator workflows are intentionally kept out of the served docs surface.

