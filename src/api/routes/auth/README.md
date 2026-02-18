# Auth Routes

## Purpose
Provides authentication and session endpoints for the API, including
registration, login, OAuth entrypoints, 2FA, and account recovery flows.

## Key Responsibilities
- Register and authenticate users
- Issue and revoke session tokens
- Handle 2FA setup, confirmation, and recovery
- Support OAuth-based login flows
- Enforce credential and account state validation

## Main Entry Points
- POST /register
- POST /login
- POST /logout
- POST /oauth/login
- POST /oauth/callback
- POST /2fa/setup
- POST /2fa/confirm
- POST /2fa/disable
- POST /sessions/revoke
- POST /sessions/revoke-all

## Dependencies
- Core auth module for account and session logic
- API middleware for current-user resolution
- Auth schemas for request/response validation

## Notes
- Routes are grouped on an APIRouter tagged Authentication.
- Error responses are standardized via common API schema types.
