# Notification Routes

Notification APIs are scoped to the authenticated user.

## Routes

- `GET /users/@me/notifications`
- `POST /users/@me/notifications/{notification_id}/read`
- `POST /users/@me/notifications/read-all`

## Purpose

These routes let clients fetch the current notification feed, mark a single notification as read, or clear all unread items for the current user.

## Typical Flow

1. fetch notifications on startup or when the app regains focus
2. mark individual notifications as read when the user visits the target content
3. use `read-all` for explicit inbox-clearing actions

## Client Guidance

- keep local unread counts synchronized with server responses
- prefer targeted read calls over repeatedly re-fetching large feeds
- pair these routes with gateway notification events when available

